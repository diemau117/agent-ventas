"""Catálogo público de la landing: se resuelve solo por public_key (spec §18).

El frontend nunca ve business_id; la clave pública identifica al tenant.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Business, Product

router = APIRouter()


def _price_label(cents: int | None, currency: str) -> str:
    # Mismo formato que _price_label en app/services/conversation.py.
    if cents is None:
        return ""
    return f"${cents / 100:,.2f} {(currency or '').strip()}".strip()


def _item(p: Product) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "price_label": _price_label(p.price_cents, p.currency),
        "category": p.category,
    }


@router.get("/catalog")
def catalog(public_key: str = "", db: Session = Depends(get_db)):
    biz = db.query(Business).filter_by(public_key=public_key).first()
    if not biz:
        raise HTTPException(status_code=401, detail="invalid_public_key")

    active = (
        db.query(Product)
        .filter_by(business_id=biz.id, active=True)
        .order_by(Product.id)
        .all()
    )
    plans = [p for p in active if p.category == "plan"]
    services = [p for p in active if p.category == "servicio"]
    # Sin categorías comerciales: todo lo activo se muestra como planes.
    if not plans and not services:
        plans = active

    return {
        "business": {
            "name": biz.name,
            "description": biz.description,
            "agent_name": biz.agent_name,
            "hours": biz.hours,
            "phone": biz.phone,
            "whatsapp": biz.whatsapp,
        },
        "plans": [_item(p) for p in plans],
        "services": [_item(p) for p in services],
    }
