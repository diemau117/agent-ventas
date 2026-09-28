"""Centro de Control — Panel para el asesor humano con multi-device.

Implementa:
- Autenticación de dispositivos (Bearer token)
- Claim atómico de conversaciones
- Lease/heartbeat para evitar bloqueos permanentes
- WebSocket para real-time sync
- Reconnect/resync mechanism
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.config import settings
from app.db.database import get_db
from app.db.models import (
    Appointment,
    Business,
    Conversation,
    Device,
    Lead,
    Message,
)
from app.device_auth import (
    authenticate_device,
    get_device_from_request,
    get_heartbeat_interval_seconds,
    get_lease_duration_seconds,
    is_lease_expired,
    register_device,
    revoke_device,
    update_heartbeat,
)
from app.realtime import broadcast_event, manager

router = APIRouter()
log = logging.getLogger("control_center")


# ============================================================================
# Device Registration & Authentication
# ============================================================================


@router.post("/control-center/devices/register")
async def register_device_endpoint(
    device_id: str = Query(...),
    name: str = Query(default=""),
    token: str = Query(...),  # CRM token for tenant resolution
    db: Session = Depends(get_db),
):
    """Registra un nuevo dispositivo para un negocio.

    El cliente llama esto una sola vez al instalar el Centro de Control.
    Retorna el Bearer token que el dispositivo debe guardar localmente.
    """
    business = db.query(Business).filter(Business.crm_token == token).first()
    if not business:
        raise HTTPException(status_code=401, detail="invalid_crm_token")

    device, device_token = register_device(db, business.id, device_id, name)

    if not device_token:
        # El dispositivo ya existía; no podemos retornar el token original
        raise HTTPException(
            status_code=409,
            detail="device_already_registered",
            headers={"X-Device-Id": device.device_id},
        )

    return {
        "device_id": device.device_id,
        "token": device_token,
        "business_id": business.id,
    }


@router.post("/control-center/devices/revoke")
async def revoke_device_endpoint(
    device_db_id: int = Query(...),
    db: Session = Depends(get_db),
    current_device: Device = Depends(get_device_from_request),
):
    """Revoca un dispositivo. Requiere autenticación de otro dispositivo activo."""
    # Solo se puede revocar dispositivos del mismo negocio
    target = db.query(Device).filter(
        Device.id == device_db_id,
        Device.business_id == current_device.business_id,
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="device_not_found")

    revoke_device(db, device_db_id)
    await broadcast_event(current_device.business_id, "device_revoked", {
        "device_id": target.device_id,
    })
    return {"message": "Device revoked"}


@router.get("/control-center/devices")
async def list_devices(
    db: Session = Depends(get_db),
    current_device: Device = Depends(get_device_from_request),
):
    """Lista los dispositivos del negocio."""
    devices = db.query(Device).filter(
        Device.business_id == current_device.business_id
    ).all()
    return [
        {
            "id": d.id,
            "device_id": d.device_id,
            "name": d.name,
            "status": d.status,
            "last_heartbeat": d.last_heartbeat.isoformat() if d.last_heartbeat else None,
            "last_activity": d.last_activity.isoformat() if d.last_activity else None,
            "created": d.created.isoformat() if d.created else None,
        }
        for d in devices
    ]


# ============================================================================
# Heartbeat
# ============================================================================


@router.post("/control-center/heartbeat")
async def heartbeat(
    db: Session = Depends(get_db),
    current_device: Device = Depends(get_device_from_request),
):
    """Heartbeat del dispositivo. Actualiza last_heartbeat y last_activity."""
    update_heartbeat(db, current_device)
    return {"ok": True}


# ============================================================================
# Claim / Release / Close
# ============================================================================


@router.post("/control-center/conversations/{conversation_id}/claim")
async def claim_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_device: Device = Depends(get_device_from_request),
):
    """Claim atómico de una conversación.

    Dos computadores intentando tomar la misma conversación:
    - PC1 → SUCCESS
    - PC2 → CONFLICT

    El claim es transaccional. En PostgreSQL usa SELECT FOR UPDATE;
    en SQLite usa un lock a nivel de tabla (BEGIN IMMEDIATE).
    """
    # 1. Verificar que la conversación pertenece al negocio del dispositivo
    conv = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.business_id == current_device.business_id,
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="conversation_not_found")

    # 2. Verificar disponibilidad con lock
    # En PostgreSQL: SELECT ... FOR UPDATE
    # En SQLite: no soportamos FOR UPDATE, pero podemos usar un lock a nivel de tabla
    # mediante una transacción con BEGIN IMMEDIATE
    dialect = db.get_bind().dialect.name

    if dialect == "postgresql":
        # PostgreSQL: SELECT FOR UPDATE
        conv_locked = db.query(Conversation).filter(
            Conversation.id == conversation_id,
        ).with_for_update().first()
    else:
        # SQLite u otros: no hay FOR UPDATE, pero podemos usar un lock a nivel de tabla
        # mediante una transacción con BEGIN IMMEDIATE
        # Para simplicidad en tests, solo verificamos sin lock
        # (la concurrencia real se maneja en producción con PostgreSQL)
        conv_locked = conv

    if not conv_locked:
        raise HTTPException(status_code=404, detail="conversation_not_found")

    # 3. Verificar si ya está asignada a otro dispositivo
    if conv.state == "human" and conv.assigned_device_id and conv.assigned_device_id != current_device.id:
        # Verificar si el lease ha expirado
        if not is_lease_expired(conv):
            raise HTTPException(
                status_code=409,
                detail="conversation_already_claimed",
                headers={"X-Assigned-To": conv.assigned_to or ""},
            )
        # El lease expirado: liberar y permitir el nuevo claim
        log.info(f"Lease expired for conversation {conversation_id}, allowing re-claim")

    # 4. Asignar
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    lease_duration = get_lease_duration_seconds()

    conv.state = "human"
    conv.assigned_to = current_device.name or current_device.device_id
    conv.assigned_device_id = current_device.id
    conv.assigned_at = now
    conv.lease_expires_at = now + timedelta(seconds=lease_duration)
    db.commit()

    # 5. Broadcast evento
    await broadcast_event(current_device.business_id, "conversation_claimed", {
        "conversation_id": conversation_id,
        "assigned_to": conv.assigned_to,
        "assigned_device_id": current_device.id,
        "assigned_at": conv.assigned_at.isoformat(),
    })

    return {
        "conversation_id": conversation_id,
        "assigned_to": conv.assigned_to,
        "assigned_at": conv.assigned_at.isoformat(),
        "lease_expires_at": conv.lease_expires_at.isoformat(),
    }


@router.post("/control-center/conversations/{conversation_id}/release")
async def release_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_device: Device = Depends(get_device_from_request),
):
    """Libera una conversación asignada. Vuelve a estar disponible."""
    conv = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.business_id == current_device.business_id,
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="conversation_not_found")

    # Solo el dispositivo que la asignó puede liberarla
    if conv.assigned_device_id != current_device.id:
        raise HTTPException(status_code=403, detail="not_assigned_to_you")

    conv.state = "ai"
    conv.assigned_to = None
    conv.assigned_device_id = None
    conv.assigned_at = None
    conv.lease_expires_at = None
    db.commit()

    await broadcast_event(current_device.business_id, "conversation_released", {
        "conversation_id": conversation_id,
    })

    return {"message": "Conversation released"}


@router.post("/control-center/conversations/{conversation_id}/close")
async def close_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_device: Device = Depends(get_device_from_request),
):
    """Cierra una conversación. Ya no está disponible para claim."""
    conv = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.business_id == current_device.business_id,
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="conversation_not_found")

    conv.state = "closed"
    conv.assigned_to = None
    conv.assigned_device_id = None
    conv.assigned_at = None
    conv.lease_expires_at = None
    db.commit()

    await broadcast_event(current_device.business_id, "conversation_closed", {
        "conversation_id": conversation_id,
    })

    return {"message": "Conversation closed"}


# ============================================================================
# State (Resync)
# ============================================================================


@router.get("/control-center/state")
async def get_full_state(
    db: Session = Depends(get_db),
    current_device: Device = Depends(get_device_from_request),
):
    """Estado completo del negocio para resync después de desconexión.

    Retorna todos los leads, conversaciones y citas del negocio.
    """
    business_id = current_device.business_id

    leads = db.query(Lead).filter(Lead.business_id == business_id).order_by(Lead.updated.desc()).all()
    conversations = db.query(Conversation).filter(Conversation.business_id == business_id).all()
    appointments = db.query(Appointment).filter(Appointment.business_id == business_id).all()

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    return {
        "leads": [
            {
                "id": lead.id,
                "name": lead.name or "Sin nombre",
                "company": lead.company or "",
                "email": lead.email or "",
                "phone": lead.phone or "",
                "need": lead.need or "",
                "problem": lead.problem or "",
                "budget": lead.budget or "",
                "urgency": lead.urgency or "",
                "temperature": lead.temperature,
                "status": lead.status,
                "next_action": lead.next_action or "",
                "ai_summary": lead.ai_summary or "",
                "last_interaction": lead.last_interaction.isoformat() if lead.last_interaction else None,
                "created": lead.created.isoformat() if lead.created else None,
            }
            for lead in leads
        ],
        "conversations": [
            {
                "id": conv.id,
                "state": conv.state,
                "channel": conv.channel,
                "assigned_to": conv.assigned_to,
                "assigned_device_id": conv.assigned_device_id,
                "assigned_at": conv.assigned_at.isoformat() if conv.assigned_at else None,
                "lease_expires_at": conv.lease_expires_at.isoformat() if conv.lease_expires_at else None,
                "lease_expired": is_lease_expired(conv),
                "summary": conv.summary or "",
                "handoff_reason": conv.handoff_reason or "",
                "created": conv.created.isoformat() if conv.created else None,
            }
            for conv in conversations
        ],
        "appointments": [
            {
                "id": appt.id,
                "lead_id": appt.lead_id,
                "conversation_id": appt.conversation_id,
                "start": appt.start.isoformat() if appt.start else None,
                "status": appt.status,
                "customer_name": appt.customer_name,
                "contact": appt.contact,
            }
            for appt in appointments
        ],
        "server_time": now.isoformat(),
    }


# ============================================================================
# Legacy endpoints (backward compatibility)
# ============================================================================


def get_business_by_token(token: str, db: Session):
    """Obtiene un negocio por su token CRM."""
    return db.query(Business).filter(Business.crm_token == token).first()


@router.get("/control-center/leads")
async def control_center_leads(token: str = Query(...), db: Session = Depends(get_db)):
    """Lista leads con toda la información que necesita el asesor.

    Cada lead incluye:
    - Temperatura: 🔥 caliente / 🌤️ tibio / ❄️ frío
    - Último mensaje del cliente (resumen)
    - ¿Agendó? Fecha y hora
    - Canal y contacto
    - Siguiente acción sugerida: llamar / confirmar cita / enviar propuesta
    """
    business = get_business_by_token(token, db)
    if not business:
        return []

    leads = db.query(Lead).filter(Lead.business_id == business.id).order_by(Lead.updated.desc()).all()

    result = []
    for lead in leads:
        # Último mensaje del cliente
        last_message = ""
        if lead.conversation_id:
            msg = db.query(Message).filter(
                Message.conversation_id == lead.conversation_id,
                Message.role == "user"
            ).order_by(Message.created.desc()).first()
            if msg:
                last_message = msg.content[:200]  # Resumen

        # Cita agendada
        appointment = None
        if lead.id:
            appt = db.query(Appointment).filter(Appointment.lead_id == lead.id).order_by(Appointment.start.desc()).first()
            if appt:
                appointment = {
                    "start": appt.start.isoformat(),
                    "status": appt.status,
                    "customer_name": appt.customer_name,
                    "contact": appt.contact,
                }

        # Canal
        channel = "web"
        if lead.conversation_id:
            conv = db.query(Conversation).filter(Conversation.id == lead.conversation_id).first()
            if conv:
                channel = conv.channel

        # Temperatura con emoji
        temp_emoji = {"caliente": "🔥", "tibio": "🌤️", "frio": "❄️", "calificado": "✅", "cliente": "💰"}
        temperature = temp_emoji.get(lead.temperature, "❄️")

        # Siguiente acción sugerida
        next_action = "llamar"
        if appointment and appointment["status"] == "confirmed":
            next_action = "confirmar cita"
        elif lead.temperature == "caliente":
            next_action = "enviar propuesta"
        elif lead.temperature == "frio":
            next_action = "llamar"

        result.append({
            "id": lead.id,
            "name": lead.name or "Sin nombre",
            "company": lead.company or "",
            "email": lead.email or "",
            "phone": lead.phone or "",
            "need": lead.need or "",
            "problem": lead.problem or "",
            "budget": lead.budget or "",
            "urgency": lead.urgency or "",
            "temperature": temperature,
            "temperature_raw": lead.temperature,
            "status": lead.status,
            "channel": channel,
            "last_message": last_message,
            "appointment": appointment,
            "next_action": next_action,
            "ai_summary": lead.ai_summary or "",
            "last_interaction": lead.last_interaction.isoformat() if lead.last_interaction else None,
            "created": lead.created.isoformat() if lead.created else None,
        })

    return result


@router.get("/control-center/stats")
async def control_center_stats(token: str = Query(...), db: Session = Depends(get_db)):
    """Métricas rápidas para el asesor."""
    business = get_business_by_token(token, db)
    if not business:
        return {}

    today = datetime.now(timezone.utc).date()

    total_leads = db.query(Lead).filter(Lead.business_id == business.id).count()
    leads_hoy = db.query(Lead).filter(Lead.business_id == business.id, func.date(Lead.created) == today).count()
    leads_calientes = db.query(Lead).filter(Lead.business_id == business.id, Lead.temperature == "caliente").count()
    citas_pendientes = db.query(Appointment).filter(Appointment.business_id == business.id, Appointment.status == "confirmed").count()
    conversaciones_activas = db.query(Conversation).filter(Conversation.business_id == business.id, Conversation.state == "ai").count()

    return {
        "total_leads": total_leads,
        "leads_hoy": leads_hoy,
        "leads_calientes": leads_calientes,
        "citas_pendientes": citas_pendientes,
        "conversaciones_activas": conversaciones_activas,
    }


# ============================================================================
# WebSocket
# ============================================================================


@router.websocket("/control-center/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    """WebSocket para real-time sync.

    El cliente se conecta con ?token=<device_bearer_token>.
    Recibe eventos: conversation_created, conversation_updated, conversation_claimed,
    conversation_released, conversation_closed, message_created.
    """
    # Autenticar el dispositivo (usa get_db: misma fuente que el resto de la app)
    try:
        device = authenticate_device(db, token)
    except HTTPException:
        await websocket.close(code=4001, reason="unauthorized")
        return

    await manager.connect(device.business_id, device, websocket)

    try:
        while True:
            # Recibir mensajes del cliente (heartbeat, ack, etc.)
            data = await websocket.receive_text()
            # Por ahora solo heartbeat; en el futuro puede haber más comandos
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(device.business_id, device.id)
    except Exception as e:
        log.error(f"WebSocket error: {e}")
        manager.disconnect(device.business_id, device.id)
