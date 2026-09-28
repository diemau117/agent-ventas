"""Controles del agente y herramientas por plan y configuración.

Implementa:
- Control de herramientas por plan
- Control de herramientas por configuración del negocio
- Límites de uso de herramientas
- Auditoría de uso de herramientas
"""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Business, EventLog


# Herramientas disponibles por plan
PLAN_TOOLS = {
    "free": ["search_products", "show_plans"],
    "starter": ["search_products", "show_plans", "create_appointment"],
    "pro": ["search_products", "show_plans", "create_appointment", "external_search"],
    "enterprise": ["search_products", "show_plans", "create_appointment", "external_search", "crm_sync"],
}

# Herramientas que requieren configuración especial
TOOL_REQUIREMENTS = {
    "external_search": "external_search_enabled",
    "crm_sync": "chatwoot_url",
}


def get_allowed_tools(db: Session, business: Business) -> list[str]:
    """Obtiene las herramientas permitidas para un negocio según su plan."""
    # Por defecto, todas las herramientas básicas
    allowed = set(PLAN_TOOLS["free"])
    
    # Agregar herramientas según el plan
    plan = getattr(business, "plan", "free") or "free"
    if plan in PLAN_TOOLS:
        allowed.update(PLAN_TOOLS[plan])
    
    # Verificar requisitos especiales
    for tool, requirement in TOOL_REQUIREMENTS.items():
        if requirement == "external_search_enabled":
            if not business.external_search_enabled:
                allowed.discard(tool)
        elif requirement == "chatwoot_url":
            if not business.chatwoot_url:
                allowed.discard(tool)
    
    return list(allowed)


def check_tool_allowed(db: Session, business: Business, tool_name: str) -> None:
    """Verifica si una herramienta está permitida para el negocio."""
    allowed = get_allowed_tools(db, business)
    if tool_name not in allowed:
        raise HTTPException(
            status_code=403,
            detail="tool_not_allowed",
            headers={"X-Tool-Name": tool_name},
        )


def log_tool_usage(
    db: Session,
    business_id: int,
    conversation_id: int,
    tool_name: str,
    success: bool,
    error: str = "",
) -> None:
    """Registra el uso de una herramienta en EventLog."""
    try:
        db.add(EventLog(
            business_id=business_id,
            conversation_id=conversation_id,
            event="tool_usage",
            message=f"tool:{tool_name}",
            level="info" if success else "error",
            decision=tool_name,
            tools={tool_name: "ok" if success else error},
        ))
        db.commit()
    except Exception:
        db.rollback()


def get_tool_usage_stats(db: Session, business_id: int, days: int = 7) -> dict:
    """Obtiene estadísticas de uso de herramientas del negocio."""
    from sqlalchemy import func
    
    since = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    since = since.replace(day=since.day - days)
    
    # Uso por herramienta
    usage = (
        db.query(
            EventLog.decision,
            func.count(EventLog.id).count,
        )
        .filter(
            EventLog.business_id == business_id,
            EventLog.event == "tool_usage",
            EventLog.created >= since,
        )
        .group_by(EventLog.decision)
        .all()
    )
    
    # Errores por herramienta
    errors = (
        db.query(
            EventLog.decision,
            func.count(EventLog.id).count,
        )
        .filter(
            EventLog.business_id == business_id,
            EventLog.event == "tool_usage",
            EventLog.level == "error",
            EventLog.created >= since,
        )
        .group_by(EventLog.decision)
        .all()
    )
    
    return {
        "usage": {row[0]: row[1] for row in usage},
        "errors": {row[0]: row[1] for row in errors},
    }


def validate_tool_params(tool_name: str, params: dict) -> dict:
    """Valida los parámetros de una herramienta."""
    validators = {
        "search_products": _validate_search_products,
        "show_plans": _validate_show_plans,
        "create_appointment": _validate_create_appointment,
        "external_search": _validate_external_search,
    }
    
    validator = validators.get(tool_name)
    if validator:
        return validator(params)
    return params


def _validate_search_products(params: dict) -> dict:
    """Valida parámetros de búsqueda de productos."""
    if "query" in params:
        params["query"] = str(params["query"])[:200]
    return params


def _validate_show_plans(params: dict) -> dict:
    """Valida parámetros de mostrar planes."""
    if "category" in params:
        allowed = ["producto", "plan", "servicio"]
        if params["category"] not in allowed:
            params["category"] = "plan"
    return params


def _validate_create_appointment(params: dict) -> dict:
    """Valida parámetros de crear cita."""
    if "start" in params:
        try:
            datetime.fromisoformat(params["start"])
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid_start_date")
    if "customer_name" in params:
        params["customer_name"] = str(params["customer_name"])[:200]
    if "contact" in params:
        params["contact"] = str(params["contact"])[:200]
    return params


def _validate_external_search(params: dict) -> dict:
    """Valida parámetros de búsqueda externa."""
    if "query" in params:
        params["query"] = str(params["query"])[:500]
    return params
