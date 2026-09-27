from pydantic import BaseModel, Field, field_validator


# H15: solo asesoras conocidas; cualquier otro valor se ignora (rotación).
ALLOWED_ADVISORS = {"Ana", "Valentina", "Camila"}


class ChatRequest(BaseModel):
    # Clave pública del tenant (spec §22): el widget la embebe en la landing.
    # El backend la resuelve a business_id; el cliente NUNCA envía el id.
    public_key: str = Field(default="", max_length=64)
    conversation_id: int | None = None
    message: str = Field(default="", max_length=2000)
    greeting: bool = False
    # Asesora fija pedida por el frontend (ej. "Ana" en la landing).
    # Si viene, se guarda en el customer y rige la conversación.
    advisor: str | None = Field(default=None, max_length=100)

    @field_validator("advisor")
    @classmethod
    def _advisor_allowlist(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v if v in ALLOWED_ADVISORS else None


class PlanCard(BaseModel):
    name: str
    description: str = ""
    price_label: str = ""
    cta: str = ""


class ChatResponse(BaseModel):
    conversation_id: int
    reply: str
    replies: list[str] = []
    intent: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    # Tarjetas interactivas (ej. planes) para renderizar en el chat.
    cards: list[PlanCard] = []
    # CRM (spec §11): el lead creado/actualizado en el turno.
    lead_id: int | None = None
    lead_temperature: str = ""
    # Handoff (spec §16): si el humano toma el control.
    handoff: bool = False
    # Jeff (§13): next_step del turno (CLOSE, SCHEDULE, HANDLE_OBJECTION…).
    next_step: str = ""
