"""Autenticación por clave pública y presupuesto diario de tokens (spec §21)."""
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_db
from app.db.models import Business, Conversation, Message

PUBLIC_KEY_HEADER = "X-Public-Key"


def business_by_key(db: Session, key: str) -> Business:
    """Resuelve el tenant por clave pública. Clave ausente o desconocida → 401.

    Usado por las rutas que traen la clave en el body (POST /api/chat) y por
    `resolve_business` (header/query). La clave solo identifica al tenant; no
    otorga acceso administrativo.
    """
    if not key:
        raise HTTPException(status_code=401, detail="invalid_public_key")
    business = db.query(Business).filter(Business.public_key == key).first()
    if business is None:
        raise HTTPException(status_code=401, detail="invalid_public_key")
    return business


def resolve_business(request: Request, db: Session = Depends(get_db)) -> Business:
    """Resuelve el negocio dueño de la clave pública (header o query param).

    La clave solo identifica el tenant para rutas públicas (widget/landing);
    no otorga acceso administrativo.
    """
    key = request.headers.get(PUBLIC_KEY_HEADER) or request.query_params.get("public_key")
    return business_by_key(db, key)


def check_daily_budget(db: Session, business: Business) -> bool:
    """True si los tokens consumidos hoy (día natural UTC) están bajo el tope."""
    budget = business.daily_token_budget or settings.daily_token_budget
    today = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    used = (
        db.query(func.coalesce(func.sum(Message.tokens_in + Message.tokens_out), 0))
        .join(Conversation, Message.conversation_id == Conversation.id)
        .filter(Conversation.business_id == business.id, Message.created >= today)
        .scalar()
    )
    return (used or 0) < budget


def enforce_budget(db: Session, business: Business) -> None:
    """Corta el turno cuando el presupuesto diario de tokens se agotó (spec §21)."""
    if not check_daily_budget(db, business):
        raise HTTPException(status_code=429, detail="daily_token_budget_exceeded")
