"""Onboarding — Crea un negocio nuevo con su token de acceso."""
import secrets
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr

from app.config import settings
from app.db.database import get_db, init_db
from app.db.models import Business, Knowledge

router = APIRouter()


def require_admin(x_admin_api_key: str = Header(None)) -> None:
    """Protege endpoints administrativos. Requiere ADMIN_API_KEY en config."""
    if not settings.admin_api_key:
        raise HTTPException(status_code=403, detail="admin_api_key_not_configured")
    if x_admin_api_key != settings.admin_api_key:
        raise HTTPException(status_code=403, detail="invalid_admin_api_key")


@router.post("/init-db")
async def initialize_database(_=Depends(require_admin)):
    """Inicializa las tablas en la base de datos. Requiere admin_api_key."""
    try:
        init_db()
        return {"message": "Base de datos inicializada exitosamente"}
    except Exception as e:
        return {"error": str(e)}


class OnboardingRequest(BaseModel):
    name: str
    email: EmailStr
    phone: str = ""
    description: str = ""
    catalog: str = ""
    agent_name: str = "Sofi"


@router.post("/onboarding")
async def create_business(
    data: OnboardingRequest,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    """Crea un negocio nuevo y devuelve su token de acceso al panel.

    Requiere admin_api_key en header X-Admin-Api-Key.
    """
    
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
