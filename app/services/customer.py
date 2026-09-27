"""Memoria del cliente: perfil por conversación, asesora fija por cliente."""
from sqlalchemy.orm import Session

from app.db.models import Conversation, Customer

ADVISORS = ("Ana", "Valentina", "Camila")


def assign_advisor(conversation_id: int) -> str:
    return ADVISORS[conversation_id % len(ADVISORS)]


def ensure_customer(db: Session, business_id: int, conv: Conversation) -> Customer:
    if conv.customer_id:
        c = db.query(Customer).filter_by(id=conv.customer_id, business_id=business_id).first()
        if c:
            return c
    c = Customer(
        business_id=business_id,
        advisor_name=assign_advisor(conv.id),
        facts={},
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    conv.customer_id = c.id
    db.commit()
    return c


def profile_text(c: Customer) -> str:
    bits = []
    if c.name:
        bits.append(f"nombre: {c.name}")
    if c.phone:
        bits.append(f"teléfono: {c.phone}")
    for k, v in (c.facts or {}).items():
        bits.append(f"{k}: {v}")
    return "; ".join(bits)
