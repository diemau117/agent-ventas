"""Schema de producción — Agente de Ventas.

Convenciones:
- Todo dato comercial se scoping por ``business_id`` (multi-tenant).
- Datetimes naive en el servidor; el contenedor corre con TZ=UTC (ver H14).
- Secretos de infraestructura viven en Settings (env), no en estas tablas.
  Las columnas ``chatwoot_*`` son configuración por negocio, no credenciales
  del sistema.
"""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

try:
    from sqlalchemy import JSON as _JSON

    JSONType = JSONB().with_variant(_JSON(), "sqlite")
except Exception:  # pragma: no cover - fallback mínimo si el dialecto no resuelve
    from sqlalchemy import JSON as JSONType  # type: ignore[no-redef]


# Categorías de la base de conocimiento (spec §3).
KNOWLEDGE_CATEGORIES = (
    "empresa",      # quiénes son, misión, datos de contacto
    "horario",      # horarios (verificador H2 usa esto como evidencia)
    "direccion",    # dirección / cobertura
    "politica",     # políticas, garantías, devoluciones
    "faq",          # preguntas frecuentes
    "proceso",      # cómo trabajamos, pasos, tiempos
    "condiciones",  # términos, contratos, facturación
    "producto",     # detalle de producto o servicio
    "general",
)

# Temperatura del lead (spec §12).
LEAD_TEMPERATURES = ("frio", "tibio", "caliente", "calificado", "cliente")
LEAD_STATUSES = ("new", "working", "qualified", "proposal", "won", "lost")
LEAD_SOURCES = ("chat", "landing", "chatwoot", "manual")

# Origen del mensaje / estado de la conversación (spec §15-16).
CONVERSATION_STATES = ("ai", "human", "closed")


class Base(DeclarativeBase):
    pass


def _new_public_key() -> str:
    """Clave publicable por tenant: identifica al negocio en el widget.

    Se genera en el cliente de SQLAlchemy (no como default de BD) para que
    funcione igual en SQLite (tests) y Postgres (prod). No es un secreto:
    viaja embebida en la landing; su único poder es resolver business_id.
    """
    import secrets

    return secrets.token_urlsafe(24)


class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    agent_name: Mapped[str] = mapped_column(String(100), default="Sofi")
    description: Mapped[str] = mapped_column(Text, default="")
    tone: Mapped[str] = mapped_column(String(200), default="cálido y profesional")

    # Clave publicable: el widget de la landing la embebe para identificar el
    # tenant. NO da acceso administrativo; solo resuelve business_id en /api/chat.
    public_key: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, default=_new_public_key
    )

    # Token de lectura del CRM por tenant (H20): con él, GET /api/leads y
    # /api/appointments solo devuelven los datos DE ESTE negocio. NULL = sin
    # token propio (los NULL repetidos no chocan con el unique). El token
    # global CRM_TOKEN sigue siendo el del operador (ve todos los tenants).
    crm_token: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, default=None
    )

    # Personalidad de la voz (spec personas): consultiva | cercana |
    # directa | empatica | experta. El turno puede forzar un override
    # puntual (app/agent/personas.dynamic_persona).
    persona: Mapped[str] = mapped_column(String(40), default="consultiva")

    # Datos comerciales que el verificador (H2) usa como evidencia.
    address: Mapped[str] = mapped_column(String(300), default="")
    hours: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(100), default="")
    whatsapp: Mapped[str] = mapped_column(String(100), default="")
    timezone: Mapped[str] = mapped_column(String(40), default="UTC")

    # Presupuesto de uso (spec §21): tope de tokens por día natural.
    daily_token_budget: Mapped[int] = mapped_column(Integer, default=200_000)

    # Integración Chatwoot por negocio (spec §15). Vacío = integración inactiva.
    chatwoot_url: Mapped[str] = mapped_column(String(300), default="")
    chatwoot_token: Mapped[str] = mapped_column(String(200), default="")
    chatwoot_inbox_id: Mapped[int] = mapped_column(Integer, default=0)

    # Búsqueda externa (spec §14): habilitada por negocio.
    external_search_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    created: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    # "producto" | "plan" | "servicio": show_plans filtra planes.
    category: Mapped[str] = mapped_column(String(40), default="producto", index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Knowledge(Base):
    """Base de conocimiento de la empresa (spec §3-4): la fuente de verdad del RAG."""

    __tablename__ = "knowledge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    category: Mapped[str] = mapped_column(String(40), default="general", index=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    # Palabras clave separadas por espacio que amplían el recall del retrieval.
    keywords: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    channel: Mapped[str] = mapped_column(String(20), default="web")
    source: Mapped[str] = mapped_column(String(20), default="widget")
    # ai | human | closed (spec §15).
    state: Mapped[str] = mapped_column(String(20), default="ai", index=True)
    # Resumen estructurado para el humano que toma el control (spec §16).
    summary: Mapped[str] = mapped_column(Text, default="")
    handoff_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    handoff_reason: Mapped[str] = mapped_column(String(200), default="")
    created: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_est: Mapped[float] = mapped_column(Float, default=0.0)
    created: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(100), default="", index=True)
    company: Mapped[str] = mapped_column(String(200), default="")
    advisor_name: Mapped[str] = mapped_column(String(100), default="")
    facts: Mapped[dict] = mapped_column(JSONType, default=dict)
    created: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Lead(Base):
    """Lead estructurado (spec §11-12). Reemplaza a LeadEvent (cutover limpio)."""

    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id"), nullable=True, index=True
    )
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="chat", index=True)

    name: Mapped[str] = mapped_column(String(200), default="")
    company: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(100), default="")

    need: Mapped[str] = mapped_column(Text, default="")          # necesidad detectada
    problem: Mapped[str] = mapped_column(Text, default="")       # dolor expresado
    service_interest: Mapped[str] = mapped_column(String(200), default="")
    budget: Mapped[str] = mapped_column(String(100), default="")
    urgency: Mapped[str] = mapped_column(String(40), default="")

    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    temperature: Mapped[str] = mapped_column(String(20), default="frio", index=True)

    next_action: Mapped[str] = mapped_column(String(300), default="")
    ai_summary: Mapped[str] = mapped_column(Text, default="")

    last_interaction: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id"), nullable=True, index=True
    )
    # CRM (spec §10): la cita queda ligada al lead que la agendó.
    lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("leads.id"), nullable=True, index=True
    )
    customer_name: Mapped[str] = mapped_column(String(200), default="")
    contact: Mapped[str] = mapped_column(String(200), default="")
    # Contexto que recibe el vendedor (spec §10).
    company: Mapped[str] = mapped_column(String(200), default="")
    need: Mapped[str] = mapped_column(Text, default="")
    context: Mapped[str] = mapped_column(Text, default="")
    start: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(20), default="confirmed")


class EventLog(Base):
    """Trazabilidad por turno (H4): intent, decisión, tools, verificación, costo."""

    __tablename__ = "event_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    request_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    event: Mapped[str] = mapped_column(String(40), index=True)
    intent: Mapped[str] = mapped_column(String(30), default="")
    decision: Mapped[str] = mapped_column(String(30), default="")
    tools: Mapped[dict] = mapped_column(JSONType, default=dict)
    issues: Mapped[list] = mapped_column(JSONType, default=list)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_est: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[str] = mapped_column(String(10), default="info")
    message: Mapped[str] = mapped_column(Text, default="")
    created: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class RateLimitBucket(Base):
    """Contador de rate-limit persistido: correcto con múltiples workers."""

    __tablename__ = "rate_limit_buckets"
    __table_args__ = (UniqueConstraint("bucket_key", "window_start", name="uq_rate_window"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bucket_key: Mapped[str] = mapped_column(String(120), index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime, index=True)
    hits: Mapped[int] = mapped_column(Integer, default=0)


class ExternalSearch(Base):
    """Auditoría de búsquedas externas (spec §14): qué se buscó y si se usó."""

    __tablename__ = "external_searches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    query: Mapped[str] = mapped_column(String(500))
    results: Mapped[list] = mapped_column(JSONType, default=list)
    answer_used: Mapped[bool] = mapped_column(Boolean, default=False)
    created: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
