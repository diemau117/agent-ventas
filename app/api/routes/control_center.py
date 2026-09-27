"""Centro de Control — Panel para el asesor humano.

Muestra leads con temperatura, último mensaje, cita agendada, canal,
contacto y siguiente acción sugerida. Todo listo para que el asesor
entre y cierre la venta sin hacer preguntas repetidas.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, date

from app.db.database import get_db
from app.db.models import Business, Lead, Appointment, Conversation, Message

router = APIRouter()


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

    today = date.today()

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
