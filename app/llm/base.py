from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import re

# Slots ISO que comparte check_availability en la respuesta (patrón de cita).
_SLOT_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


@dataclass
class LLMToolCall:
    name: str
    args: dict
    id: str = ""


@dataclass
class LLMResult:
    text: str = ""
    tool_calls: list[LLMToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {"input": 0, "output": 0, "cost_est": 0.0})


class LLMProvider(ABC):
    @abstractmethod
    async def chat(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> LLMResult: ...


class FakeProvider(LLMProvider):
    """Doble determinista para tests y dev sin key.

    1ª llamada con tools: emite search_products si el mensaje huele a
    producto/precio, create_lead si trae contacto, get_business_info si no.
    2ª llamada (ya hay datos autorizados): redacta respuesta breve con ellos.
    """

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResult:
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        low = last_user.lower()
        has_data = any("Información autorizada (" in m.get("content", "") for m in messages)
        if tools:
            offered = {t.get("function", {}).get("name") for t in tools}
            # Confirmación con slots ya ofrecidos en la conversación → crear la
            # cita. Va antes de todo: el historial persistido trae los slots y
            # "Información autorizada (check_availability)" (has_data sería True).
            all_text = "\n".join(m.get("content", "") for m in messages)
            slots = _SLOT_RE.findall(all_text)
            confirms = any(k in low for k in ("sí", "si,", "dale", "confirmo", "perfecto", "de una", "agendar", "agendá"))
            if slots and confirms and "create_appointment" in offered:
                hour = next((h for h in ("10:00", "15:00") if h in last_user), None)
                chosen = next((s for s in slots if (f"T{hour}:" in s if hour else True)), slots[0])
                return LLMResult(tool_calls=[LLMToolCall("create_appointment", {"start": chosen})])
        if tools and not has_data:
            offered = {t.get("function", {}).get("name") for t in tools}
            if offered == {"update_customer"}:
                # Rama smalltalk: solo guardar datos personales, si los hay.
                if any(k in low for k in ("me llamo", "mi nombre", "mi negocio", "tengo una", "tengo un", "soy ")):
                    return LLMResult(tool_calls=[LLMToolCall("update_customer", {"name": "Demo", "facts": {"nota": last_user[:200]}})])
                return LLMResult(text="¡Hola! Soy Demo, del equipo. ¿Cómo te llamás y qué negocio tenés?")
            if any(k in low for k in ("agendar", "agenda", "llamada", "cita", "reunión", "reunion", "horario")):
                return LLMResult(tool_calls=[LLMToolCall("check_availability", {})])
            if any(k in low for k in ("@example", "@test", "55", "llámame", "llamame", "contacto", "mi nombre es", "me llamo")):
                return LLMResult(tool_calls=[LLMToolCall("create_lead", {"name": "Demo", "contact": "demo@test.com", "note": last_user[:200]})])
            if any(k in low for k in ("precio", "cuesta", "cuánto", "cuanto", "busco", "quiero", "recomienda", "producto", "café", "cafe", "taza", "cafetera", "curso", "suscripción")):
                return LLMResult(tool_calls=[LLMToolCall("search_products", {"query": last_user[:100]})])
            return LLMResult(tool_calls=[LLMToolCall("get_business_info", {})])
        for m in reversed(messages):
            if "Información autorizada (" in m.get("content", ""):
                return LLMResult(text=f"Con gusto. Según nuestra información: {m['content'][:300]}")
        return LLMResult(text="Hola, ¿en qué te puedo ayudar?")
