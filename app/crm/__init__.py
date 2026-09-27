"""CRM: lead estructurado y temperatura (spec §11-12)."""

from app.crm.leads import extract_contact, lead_brief, record_interaction, upsert_lead
from app.crm.temperature import apply_temperature, score_temperature, signals_from_conversation

__all__ = [
    "upsert_lead",
    "extract_contact",
    "record_interaction",
    "lead_brief",
    "score_temperature",
    "apply_temperature",
    "signals_from_conversation",
]
