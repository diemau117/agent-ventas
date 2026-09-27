"""Panel de Control — Dashboard, leads, citas y configuración del negocio."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from datetime import datetime, date

from app.db.database import get_db
from app.db.models import Business, Lead, Appointment, Conversation

router = APIRouter()


def get_business_by_token(token: str, db: Session):
    """Obtiene un negocio por su token CRM."""
    return db.query(Business).filter(Business.crm_token == token).first()


class ConfigUpdate(BaseModel):
    name: str = None
    agent_name: str = None
    phone: str = None
    whatsapp: str = None
    description: str = None


@router.get("/panel/dashboard")
async def dashboard(token: str = Query(...), db: Session = Depends(get_db)):
    """Métricas principales del negocio."""
    business = get_business_by_token(token, db)
    if not business:
        return {"error": "Token inválido"}
    
    leads_count = db.query(Lead).filter(Lead.business_id == business.id).count()
    citas_count = db.query(Appointment).filter(Appointment.business_id == business.id).count()
    today = date.today()
    conversaciones_hoy = db.query(Conversation).filter(
        Conversation.business_id == business.id,
        func.date(Conversation.created) == today
    ).count()
    
    return {
        "leads": leads_count,
        "citas": citas_count,
        "conversaciones_hoy": conversaciones_hoy,
    }


@router.get("/panel/leads")
async def list_leads(token: str = Query(...), db: Session = Depends(get_db)):
    """Lista de leads del negocio."""
    business = get_business_by_token(token, db)
    if not business:
        return []
    
    leads = db.query(Lead).filter(Lead.business_id == business.id).order_by(Lead.created.desc()).all()
    return leads


@router.get("/panel/appointments")
async def list_appointments(token: str = Query(...), db: Session = Depends(get_db)):
    """Lista de citas del negocio."""
    business = get_business_by_token(token, db)
    if not business:
        return []
    
    citas = db.query(Appointment).filter(Appointment.business_id == business.id).order_by(Appointment.start.desc()).all()
    return citas


@router.get("/panel/config")
async def get_config(token: str = Query(...), db: Session = Depends(get_db)):
    """Configuración del negocio."""
    business = get_business_by_token(token, db)
    if not business:
        return {"error": "Token inválido"}
    
    return {
        "name": business.name,
        "agent_name": business.agent_name,
        "phone": business.phone,
        "whatsapp": business.whatsapp,
        "description": business.description,
    }


@router.post("/panel/config")
async def update_config(data: ConfigUpdate, token: str = Query(...), db: Session = Depends(get_db)):
    """Actualiza la configuración del negocio."""
    business = get_business_by_token(token, db)
    if not business:
        return {"error": "Token inválido"}
    
    if data.name is not None:
        business.name = data.name
    if data.agent_name is not None:
        business.agent_name = data.agent_name
    if data.phone is not None:
        business.phone = data.phone
    if data.whatsapp is not None:
        business.whatsapp = data.whatsapp
    if data.description is not None:
        business.description = data.description
    
    db.commit()
    return {"message": "Configuración actualizada"}
