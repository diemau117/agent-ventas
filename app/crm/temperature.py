"""Temperatura del lead (spec §12): scoring determinista, sin LLM."""

import re
import unicodedata

from sqlalchemy.orm import Session

from app.db.models import Appointment, Conversation, Lead, Message

# Señales de texto derivadas de los mensajes del usuario (normalizado sin acentos).
_PRICE_TERMS = ("$", "precio", "cuesta", "cuanto")
_PROBLEM_TERMS = ("problema", "pierdo", "no me", "necesito", "quiero")
_BUDGET_TERMS = ("presupuesto",)
_URGENCY_TERMS = ("urgente", "esta semana")
# "ya" solo como palabra suelta: como subcadena pegaría en "ayer", "mayo", etc.
_URGENCY_WORD = re.compile(r"\bya\b")


def _norm(text: str) -> str:
    """Minúsculas y sin acentos: "cuánto" == "cuanto"."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def score_temperature(signals: dict) -> str:
    """Devuelve una de LEAD_TEMPERATURES a partir de señales (spec §12).

    Claves opcionales (default False; `message_count` default 0):
    has_contact, has_appointment, has_budget, has_urgency, asked_price,
    stated_problem, ready_to_buy, converted (bool), message_count (int).

    Reglas, evaluadas en este orden:
    1. `converted`                                  → "cliente"
    2. `has_contact` y (`ready_to_buy` o `has_appointment`) → "calificado"
    3. `has_contact`                                → "caliente"
    4. `stated_problem` o `asked_price`             → "caliente"
    5. `has_budget` o `has_urgency` o `message_count >= 4` → "tibio"
    6. si no                                        → "frio"
    """
    s = signals or {}
    if s.get("converted"):
        return "cliente"
    if s.get("has_contact") and (s.get("ready_to_buy") or s.get("has_appointment")):
        return "calificado"
    if s.get("has_contact"):
        return "caliente"
    if s.get("stated_problem") or s.get("asked_price"):
        return "caliente"
    if s.get("has_budget") or s.get("has_urgency") or int(s.get("message_count") or 0) >= 4:
        return "tibio"
    return "frio"


def apply_temperature(db: Session, lead: Lead, signals: dict) -> Lead:
    """Recalcula la temperatura del lead con `signals` y la persiste."""
    lead.temperature = score_temperature(signals)
    db.commit()
    return lead


def signals_from_conversation(
    db: Session, business_id: int, conversation_id: int, ready_to_buy: bool = False
) -> dict:
    """Deriva las señales de scoring desde la BD, aislado por business_id.

    - has_contact: algún Lead de la conversación con email o phone.
    - has_appointment: Appointment "confirmed" de esa conversación.
    - asked_price: algún Message role=user con "$", "precio", "cuesta" o "cuánto".
    - stated_problem: Message role=user con "problema", "pierdo", "no me",
      "necesito" o "quiero".
    - has_budget: Message role=user que menciona "presupuesto".
    - has_urgency: Message role=user con "urgente", "ya" (palabra suelta) o
      "esta semana".
    - message_count: cantidad de Messages de la conversación (todos los roles).
    - converted: True solo si algún Lead de la conversación tiene status "won".
    - ready_to_buy: lo aporta el orquestador desde la decisión de Jeff
      (CLOSE/CAPTURE_CONTACT con contacto = listo para comprar).
    """
    leads = (
        db.query(Lead)
        .filter(Lead.business_id == business_id, Lead.conversation_id == conversation_id)
        .all()
    )
    has_contact = any(lead.email or lead.phone for lead in leads)
    converted = any(lead.status == "won" for lead in leads)

    has_appointment = (
        db.query(Appointment)
        .filter(
            Appointment.business_id == business_id,
            Appointment.conversation_id == conversation_id,
            Appointment.status == "confirmed",
        )
        .first()
        is not None
    )

    # Message no tiene business_id: se une por Conversation para respetar el tenant.
    messages = (
        db.query(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .filter(
            Conversation.business_id == business_id,
            Message.conversation_id == conversation_id,
        )
        .all()
    )
    user_texts = [_norm(m.content) for m in messages if m.role == "user"]

    asked_price = any(term in text for text in user_texts for term in _PRICE_TERMS)
    stated_problem = any(term in text for text in user_texts for term in _PROBLEM_TERMS)
    has_budget = any(term in text for text in user_texts for term in _BUDGET_TERMS)
    has_urgency = any(
        any(term in text for term in _URGENCY_TERMS) or _URGENCY_WORD.search(text)
        for text in user_texts
    )

    return {
        "has_contact": has_contact,
        "has_appointment": has_appointment,
        "has_budget": has_budget,
        "has_urgency": has_urgency,
        "asked_price": asked_price,
        "stated_problem": stated_problem,
        "ready_to_buy": bool(ready_to_buy),
        "converted": converted,
        "message_count": len(messages),
    }
