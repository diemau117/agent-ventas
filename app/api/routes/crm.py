"""CRM de lectura: leads y citas para el humano (spec §10-12).

Auth (H20, multi-tenant): el token resuelve el `business_id` autenticado y
las queries se filtran por ese tenant. Dos clases de token:

- `Business.crm_token` (por negocio): solo devuelve leads/citas de ESE
  negocio. Es el que se le entrega a cada cliente.
- `settings.crm_token` (CRM_TOKEN global, del operador): ve todos los
  tenants — es la credencial del servidor, nunca se distribuye a clientes.

Si ninguno está configurado los endpoints están deshabilitados
(403 crm_disabled) — la public_key de la landing no basta: los leads
contienen teléfonos/emails y esa clave vive en el JS público del widget.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_db
from app.db.models import Appointment, Business, Lead

router = APIRouter()


def _require_crm(token: str, db: Session) -> int | None:
    """Valida el token y devuelve el business_id a filtrar (None = operador, ve todo).

    Sin token configurado en ninguna parte → 403 crm_disabled. Token presente
    pero que no matchea con ningún tenant ni con el global → 401.
    """
    import secrets

    if not token:
        if not settings.crm_token and not _any_tenant_token(db):
            raise HTTPException(status_code=403, detail="crm_disabled")
        raise HTTPException(status_code=401, detail="invalid_crm_token")
    # Token de operador: ve todos los tenants (compare_digest por timing).
    if settings.crm_token and secrets.compare_digest(token, settings.crm_token):
        return None
    # Token de tenant: lookup en BD + compare_digest (el lookup solo prefiltra).
    row = db.query(Business).filter(Business.crm_token == token).first()
    if row is not None and row.crm_token and secrets.compare_digest(token, row.crm_token):
        return row.id
    if not settings.crm_token and not _any_tenant_token(db):
        raise HTTPException(status_code=403, detail="crm_disabled")
    raise HTTPException(status_code=401, detail="invalid_crm_token")


def _any_tenant_token(db: Session) -> bool:
    return (
        db.query(Business.id).filter(Business.crm_token.is_not(None)).first() is not None
    )


def _lead_dict(lead: Lead) -> dict:
    return {
        "id": lead.id,
        "conversation_id": lead.conversation_id,
        "name": lead.name,
        "company": lead.company,
        "email": lead.email,
        "phone": lead.phone,
        "need": lead.need,
        "problem": lead.problem,
        "service_interest": lead.service_interest,
        "status": lead.status,
        "temperature": lead.temperature,
        "next_action": lead.next_action,
        "ai_summary": lead.ai_summary,
        "last_interaction": lead.last_interaction.isoformat() if lead.last_interaction else None,
        "created": lead.created.isoformat() if lead.created else None,
    }


def _appt_dict(a: Appointment) -> dict:
    return {
        "id": a.id,
        "lead_id": a.lead_id,
        "conversation_id": a.conversation_id,
        "start": a.start.isoformat(timespec="seconds"),
        "status": a.status,
        "customer_name": a.customer_name,
        "contact": a.contact,
        "company": a.company,
        "need": a.need,
        "context": a.context,
    }


@router.get("/leads")
def list_leads(
    token: str = "",
    status: str = "",
    temperature: str = "",
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db),
):
    """Leads ordenados por última interacción (los más recientes primero)."""
    scope = _require_crm(token, db)
    q = db.query(Lead).order_by(Lead.last_interaction.desc().nullslast(), Lead.id.desc())
    if scope is not None:
        q = q.filter(Lead.business_id == scope)
    if status:
        q = q.filter(Lead.status == status)
    if temperature:
        q = q.filter(Lead.temperature == temperature)
    return {"leads": [_lead_dict(lead) for lead in q.limit(limit).all()]}


@router.get("/appointments")
def list_appointments(
    token: str = "",
    date_from: str = "",
    date_to: str = "",
    status: str = "confirmed",
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db),
):
    """Próximas citas del calendario, ordenadas por start ascendente."""
    scope = _require_crm(token, db)
    q = db.query(Appointment).order_by(Appointment.start.asc())
    if scope is not None:
        q = q.filter(Appointment.business_id == scope)
    if status:
        q = q.filter(Appointment.status == status)
    if date_from:
        try:
            q = q.filter(Appointment.start >= datetime.fromisoformat(date_from))
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid_date_from")
    if date_to:
        try:
            q = q.filter(Appointment.start <= datetime.fromisoformat(date_to))
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid_date_to")
    return {"appointments": [_appt_dict(a) for a in q.limit(limit).all()]}
