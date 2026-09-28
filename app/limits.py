"""Límites de uso y consumo por negocio y conversación (spec §21).

Implementa:
- Límite de mensajes por conversación
- Límite de conversaciones por negocio
- Límite de tokens por conversación
- Control de herramientas por plan
"""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Business, Conversation, Message


def check_message_limit(db: Session, conversation_id: int) -> None:
    """Verifica que la conversación no exceda el límite de mensajes."""
    max_messages = settings.max_messages_per_conversation
    count = db.query(Message).filter(Message.conversation_id == conversation_id).count()
    if count >= max_messages:
        raise HTTPException(
            status_code=429,
            detail="message_limit_exceeded",
            headers={"X-Limit-Type": "messages_per_conversation"},
        )


def check_conversation_limit(db: Session, business_id: int) -> None:
    """Verifica que el negocio no exceda el límite de conversaciones activas."""
    max_conversations = settings.max_active_conversations_per_business
    count = db.query(Conversation).filter(
        Conversation.business_id == business_id,
        Conversation.state == "ai",
    ).count()
    if count >= max_conversations:
        raise HTTPException(
            status_code=429,
            detail="conversation_limit_exceeded",
            headers={"X-Limit-Type": "active_conversations"},
        )


def check_token_limit(db: Session, conversation_id: int) -> None:
    """Verifica que la conversación no exceda el límite de tokens."""
    max_tokens = settings.max_tokens_per_conversation
    used = (
        db.query(func.coalesce(func.sum(Message.tokens_in + Message.tokens_out), 0))
        .filter(Message.conversation_id == conversation_id)
        .scalar()
    )
    if (used or 0) >= max_tokens:
        raise HTTPException(
            status_code=429,
            detail="token_limit_exceeded",
            headers={"X-Limit-Type": "tokens_per_conversation"},
        )


def check_business_limits(db: Session, business: Business) -> None:
    """Verifica todos los límites del negocio antes de procesar un turno."""
    # Límite de conversaciones activas
    check_conversation_limit(db, business.id)
    
    # Límite diario de tokens
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
    if (used or 0) >= budget:
        raise HTTPException(
            status_code=429,
            detail="daily_token_budget_exceeded",
            headers={"X-Limit-Type": "daily_token_budget"},
        )


def check_conversation_limits(db: Session, conversation_id: int) -> None:
    """Verifica los límites de la conversación antes de procesar un turno."""
    check_message_limit(db, conversation_id)
    check_token_limit(db, conversation_id)


def get_usage_stats(db: Session, business_id: int) -> dict:
    """Obtiene estadísticas de uso del negocio."""
    today = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    
    # Tokens usados hoy
    tokens_today = (
        db.query(func.coalesce(func.sum(Message.tokens_in + Message.tokens_out), 0))
        .join(Conversation, Message.conversation_id == Conversation.id)
        .filter(Conversation.business_id == business_id, Message.created >= today)
        .scalar()
    )
    
    # Conversaciones activas
    active_conversations = db.query(Conversation).filter(
        Conversation.business_id == business_id,
        Conversation.state == "ai",
    ).count()
    
    # Total de conversaciones
    total_conversations = db.query(Conversation).filter(
        Conversation.business_id == business_id,
    ).count()
    
    # Total de mensajes
    total_messages = (
        db.query(func.count(Message.id))
        .join(Conversation, Message.conversation_id == Conversation.id)
        .filter(Conversation.business_id == business_id)
        .scalar()
    )
    
    # Presupuesto DIARIO del negocio (Business.daily_token_budget) con fallback
    # al global: es el mismo valor que usa check_business_limits para cortar.
    # (Antes devolvía el global siempre y el panel mostraba un % falso.)
    business = db.query(Business).filter(Business.id == business_id).first()
    budget = (business.daily_token_budget if business else None) or settings.daily_token_budget

    return {
        "tokens_today": tokens_today or 0,
        "daily_token_budget": budget,
        "active_conversations": active_conversations,
        "total_conversations": total_conversations,
        "total_messages": total_messages or 0,
    }
