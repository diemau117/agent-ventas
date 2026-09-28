"""Onboarding — Crea un negocio nuevo con su token de acceso."""
import secrets
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr

from app.db.database import get_db
from app.db.models import Business, Knowledge

router = APIRouter()


class OnboardingRequest(BaseModel):
    name: str
    email: EmailStr
    phone: str = ""
    description: str = ""
    catalog: str = ""
    agent_name: str = "Sofi"


@router.post("/onboarding")
async def create_business(data: OnboardingRequest, db: Session = Depends(get_db)):
    """Crea un negocio nuevo y devuelve su token de acceso al panel."""
    
    # Verificar si ya existe un negocio con ese email
    existing = db.query(Business).filter(Business.name == data.name).first()
    if existing:
        return {"error": "Ya existe un negocio con ese nombre", "crm_token": existing.crm_token}
    
    # Crear el negocio
    business = Business(
        name=data.name,
        agent_name=data.agent_name,
        description=data.description,
        phone=data.phone,
        crm_token=secrets.token_urlsafe(32),
    )
    db.add(business)
    db.flush()
    
    # Si hay catálogo, crearlo como Knowledge
    if data.catalog:
        knowledge = Knowledge(
            business_id=business.id,
            category="general",
            title="Catálogo y servicios",
            content=data.catalog,
            keywords=data.description,
        )
        db.add(knowledge)
    
    db.commit()
    
    return {
        "id": business.id,
        "crm_token": business.crm_token,
        "public_key": business.public_key,
        "message": "Negocio creado exitosamente"
    }
