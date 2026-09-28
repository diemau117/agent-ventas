"""Autenticación de dispositivos para el Centro de Control.

Cada computador que instala el Centro de Control se registra como un Device
perteneciente a un negocio. El dispositivo se autentica con un Bearer token
(que se hashea en la BD). Un dispositivo revocado no puede continuar accediendo.
"""
import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_db
from app.db.models import Business, Device


def _hash_token(token: str) -> str:
    """Hash SHA-256 del token para almacenar en BD."""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_device_token() -> str:
    """Genera un token de dispositivo seguro."""
    return secrets.token_urlsafe(32)


def register_device(
    db: Session,
    business_id: int,
    device_id: str,
    name: str = "",
) -> tuple[Device, str]:
    """Registra un nuevo dispositivo para un negocio.

    Retorna (device, token_plano). El token_plano se muestra UNA sola vez
    al cliente; en la BD solo se guarda el hash.
    """
    # Verificar si ya existe un dispositivo con ese device_id para este negocio
    existing = db.query(Device).filter(
        Device.business_id == business_id,
        Device.device_id == device_id,
    ).first()

    if existing:
        # Si existe y está activo, retornar el mismo (no regenerar token)
        if existing.status == "active":
            # No podemos retornar el token original (solo tenemos el hash)
            # El cliente debe guardar su token original
            return existing, ""
        # Si está revocado, reactivarlo
        existing.status = "active"
        existing.revoked_at = None
        existing.last_activity = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        db.refresh(existing)
        return existing, ""

    # Crear nuevo dispositivo
    token = generate_device_token()
    token_hash = _hash_token(token)

    device = Device(
        business_id=business_id,
        device_id=device_id,
        token_hash=token_hash,
        status="active",
        name=name,
        last_heartbeat=datetime.now(timezone.utc).replace(tzinfo=None),
        last_activity=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    return device, token


def authenticate_device(
    db: Session,
    token: str,
) -> Device:
    """Autentica un dispositivo por su Bearer token.

    Retorna el Device si es válido y activo.
    Levanta HTTPException 401 si el token es inválido o el dispositivo está revocado.
    """
    if not token:
        raise HTTPException(status_code=401, detail="device_token_required")

    token_hash = _hash_token(token)
    device = db.query(Device).filter(Device.token_hash == token_hash).first()

    if not device:
        raise HTTPException(status_code=401, detail="invalid_device_token")

    if device.status == "revoked":
        raise HTTPException(status_code=403, detail="device_revoked")

    return device


def revoke_device(db: Session, device_id: int) -> None:
    """Revoca un dispositivo. No puede continuar accediendo."""
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="device_not_found")

    device.status = "revoked"
    device.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()


def get_device_from_request(
    request: Request,
    db: Session = Depends(get_db),
) -> Device:
    """Extrae y autentica el dispositivo desde el header Authorization: Bearer <token>."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="bearer_token_required")

    token = auth_header[7:]  # Remove "Bearer "
    return authenticate_device(db, token)


def update_heartbeat(db: Session, device: Device) -> None:
    """Actualiza el último heartbeat del dispositivo."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    device.last_heartbeat = now
    device.last_activity = now
    db.commit()


def is_lease_expired(conversation) -> bool:
    """Verifica si el lease de una conversación ha expirado."""
    if not conversation.lease_expires_at:
        return False
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return conversation.lease_expires_at < now


def get_lease_duration_seconds() -> int:
    """Duración del lease en segundos (configurable)."""
    return getattr(settings, "device_lease_duration_seconds", 300)  # 5 min default


def get_heartbeat_interval_seconds() -> int:
    """Intervalo de heartbeat en segundos (configurable)."""
    return getattr(settings, "device_heartbeat_interval_seconds", 60)  # 1 min default


# ============================================================================
# Tickets de WebSocket (one-time, corta vida)
# ============================================================================
# El Bearer token es permanente: NO debe viajar en la URL del WebSocket
# (las URLs terminan en logs de proxy, CDNs y herramientas de diagnóstico).
# En su lugar el cliente pide un ticket de un solo uso, válido pocos segundos.
#
# Almacenamiento en memoria: Render Free corre un solo proceso, así que es
# suficiente. Si algún día hay múltiples instancias, moverlo a Redis/BD.

WS_TICKET_TTL_SECONDS = 60
_WS_TICKETS: dict[str, tuple[int, float]] = {}  # ticket -> (device_pk, expira_en_epoch)
_WS_TICKET_MAX = 10_000


def _prune_tickets(now: float) -> None:
    """Elimina tickets expirados y aplica un tope de tamaño."""
    expired = [t for t, (_, exp) in _WS_TICKETS.items() if exp <= now]
    for t in expired:
        _WS_TICKETS.pop(t, None)
    # Tope de seguridad: si alguien dispara emisiones masivas, descarta los más viejos.
    while len(_WS_TICKETS) > _WS_TICKET_MAX:
        oldest = min(_WS_TICKETS, key=lambda k: _WS_TICKETS[k][1])
        _WS_TICKETS.pop(oldest, None)


def create_ws_ticket(device_pk: int, ttl: int | None = None) -> str:
    """Emite un ticket de WebSocket de un solo uso para un dispositivo."""
    now = time.time()
    _prune_tickets(now)
    ticket = secrets.token_urlsafe(32)
    _WS_TICKETS[ticket] = (device_pk, now + (ttl or WS_TICKET_TTL_SECONDS))
    return ticket


def consume_ws_ticket(ticket: str, db: Session) -> Device:
    """Canjea un ticket por su Device. De un solo uso: se consume siempre.

    Lanza HTTPException 401 si es inválido/expirado y 403 si el dispositivo
    fue revocado (el estado se re-verifica en cada conexión, no se cachea
    dentro del ticket).
    """
    if not ticket:
        raise HTTPException(status_code=401, detail="ws_ticket_required")

    now = time.time()
    entry = _WS_TICKETS.pop(ticket, None)  # un solo uso, se consume pase lo que pase
    if not entry or entry[1] <= now:
        raise HTTPException(status_code=401, detail="ws_ticket_invalid_or_expired")

    device = db.query(Device).filter(Device.id == entry[0]).first()
    if not device:
        raise HTTPException(status_code=401, detail="device_not_found")
    if device.status == "revoked":
        raise HTTPException(status_code=403, detail="device_revoked")
    return device
