"""Lead estructurado (spec §11): upsert idempotente por conversación y brief para handoff (§16)."""

from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import LEAD_SOURCES, Lead

# Truncado a la longitud de columna del schema congelado.
# Text (need/problem/ai_summary) se limita a 2000 para no guardar basura libre.
_MAX_LEN = {
    "name": 200,
    "company": 200,
    "email": 200,
    "phone": 100,
    "budget": 100,
    "urgency": 40,
    "service_interest": 200,
    "next_action": 300,
    "status": 20,
    "temperature": 20,
    "need": 2000,
    "problem": 2000,
    "ai_summary": 2000,
}

# Campos editables vía **fields. Todo lo demás lo maneja el modelo/orquestador.
_UPDATABLE = frozenset(_MAX_LEN)


def _clean(field: str, value) -> str:
    """String truncado a la longitud de columna de `field`."""
    if not isinstance(value, str):
        value = str(value)
    return value[: _MAX_LEN[field]]


def upsert_lead(
    db: Session,
    business_id: int,
    conversation_id: int | None,
    customer_id: int | None = None,
    source: str = "chat",
    **fields,
) -> Lead:
    """Crea o actualiza el lead de una conversación (spec §11).

    Idempotente por `(business_id, conversation_id)` cuando `conversation_id`
    no es None: si ya existe, solo se actualizan los campos no vacíos recibidos
    (nunca se pisan datos existentes con vacíos); si no, se crea uno nuevo.
    Con `conversation_id` None siempre crea un lead nuevo.

    - `source` validado contra LEAD_SOURCES; inválido cae en "chat".
    - Los valores se truncan a la longitud de columna (need/problem/ai_summary
      a 2000).
    - Campos desconocidos en **fields se ignoran.
    """
    if source not in LEAD_SOURCES:
        source = "chat"

    lead = None
    if conversation_id is not None:
        lead = (
            db.query(Lead)
            .filter(Lead.business_id == business_id, Lead.conversation_id == conversation_id)
            .first()
        )

    if lead is None:
        lead = Lead(
            business_id=business_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            source=source,
        )
        db.add(lead)
    elif customer_id is not None and not lead.customer_id:
        # Completa el customer_id solo si el lead no lo tenía: nunca pisar.
        lead.customer_id = customer_id

    for field, value in fields.items():
        if field in _UPDATABLE and value:
            setattr(lead, field, _clean(field, value))

    db.commit()
    return lead


def extract_contact(value: str) -> tuple[str, str]:
    """Separa un dato de contacto en (email, phone): contiene '@' → email, si no → phone."""
    value = (value or "").strip()
    if "@" in value:
        return value, ""
    return "", value


def record_interaction(
    db: Session,
    lead: Lead,
    *,
    summary: str = "",
    next_action: str = "",
    need: str = "",
    problem: str = "",
    service_interest: str = "",
    budget: str = "",
    urgency: str = "",
    name: str = "",
    company: str = "",
) -> Lead:
    """Registra una interacción: setea last_interaction y los campos provistos.

    Solo se escriben campos no vacíos: un resumen en blanco nunca pisa datos
    existentes. `summary` guarda en ai_summary.
    """
    lead.last_interaction = datetime.now()
    updates = {
        "ai_summary": summary,
        "next_action": next_action,
        "need": need,
        "problem": problem,
        "service_interest": service_interest,
        "budget": budget,
        "urgency": urgency,
        "name": name,
        "company": company,
    }
    for field, value in updates.items():
        if value:
            setattr(lead, field, _clean(field, value))
    db.commit()
    return lead


def lead_brief(lead: Lead) -> str:
    """Resumen compacto en español del lead para el humano que recibe el handoff (§16).

    Omite campos vacíos; una línea por campo: `Etiqueta: valor`.
    """
    labels = (
        ("Nombre", lead.name),
        ("Empresa", lead.company),
        ("Necesidad", lead.need),
        ("Problema", lead.problem),
        ("Servicio de interés", lead.service_interest),
        ("Presupuesto", lead.budget),
        ("Urgencia", lead.urgency),
        ("Temperatura", lead.temperature),
        ("Estado", lead.status),
        ("Próximo paso", lead.next_action),
    )
    return "\n".join(f"{label}: {value}" for label, value in labels if value)
