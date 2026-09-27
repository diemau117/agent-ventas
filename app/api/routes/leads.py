"""Captación de leads desde la landing (spec §18).

Recibe el formulario público por public_key y guarda el Lead con
source="landing". Nunca expone business_id ni datos internos en la respuesta.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Business, Lead

router = APIRouter()


class LeadCreate(BaseModel):
    public_key: str = ""
    name: str = Field(default="", max_length=200)
    company: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=100)
    need: str = ""
    message: str = ""


@router.post("/leads", status_code=201)
def create_lead(payload: LeadCreate, db: Session = Depends(get_db)):
    biz = db.query(Business).filter_by(public_key=payload.public_key).first()
    if not biz:
        raise HTTPException(status_code=401, detail="invalid_public_key")

    name = payload.name.strip()
    email = payload.email.strip()
    phone = payload.phone.strip()
    if not (name or email or phone):
        raise HTTPException(status_code=422, detail="contact_required")

    lead = Lead(
        business_id=biz.id,
        conversation_id=None,
        source="landing",
        name=name,
        company=payload.company.strip(),
        email=email,
        phone=phone,
        need=payload.need.strip() or payload.message.strip(),
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return {"lead_id": lead.id, "status": lead.status}
