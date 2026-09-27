"""Reglas deterministas: el backend decide, el LLM solo redacta."""
import re
import unicodedata
from datetime import datetime
from typing import Literal

Intent = Literal[
    "greeting", "objection", "comparison", "handoff", "conversion",
    "pricing", "smalltalk", "support", "discovery", "general",
]

# --- Objeción: frena la compra. Va ANTES que pricing. ---
_OBJECTION = (
    "caro", "carísimo", "muy caro", "está caro", "sale caro", "sale muy caro",
    "más barato", "lo hacen más barato",
    "descuento", "rebaja", "algún descuento",
    "no tengo plata", "no tengo presupuesto", "sin presupuesto",
    "no puedo pagar", "no me alcanza",
    "después veo", "lo pienso", "déjame pensarlo", "me lo pienso",
    "ya tengo", "ya tengo proveedor", "ya trabajo con",
    "no me convence", "no estoy seguro", "no sé si",
    "es mucho", "me parece mucho",
)

# --- Comparación: quiere justificar la elección. ---
_COMPARISON = (
    "en qué se diferencian", "en qué te diferencias", "en qué se diferencia",
    "por qué tú", "por qué ustedes", "por qué debería",
    "mejor que", "comparado con", "en comparación",
    "otro chatbot", "otros chatbots", "otra agencia", "otra empresa",
    "qué los hace diferentes", "qué te hace diferente",
    "por qué los elijo", "por qué elegirlos",
    "vs ", " versus ",
)

# --- Handoff: exige VERBO/petición de contacto, no mera mención de canal.
# "mi whatsapp es 351..." NO es handoff (era el bug del audit P1).
# Booking ("agenda una llamada") queda para conversion, no acá.
_HANDOFF = (
    "hablar con", "hablo con", "habla con", "hablás con", "hablas con",
    "que me llamen", "que me llame", "llámenme", "llamarme",
    "quiero que me llamen", "quiero que me llame",
    "una persona real", "un humano", "una persona de verdad",
    "pásame con", "páseme con", "comunícame con",
)

# --- Conversión: booking/compra. Palabras del flujo de citas (BOOKING_WORDS). ---
_CONVERSION = (
    "comprar", "compro", "reservar", "reserva", "cita", "apartar", "pedido",
    "agendar", "agenda", "llamada", "reunion", "reunión",
)

# --- Pricing: exige contexto real de precio, no la palabra "cuesta" suelta.
# Cubre el falso positivo "me cuesta conseguir clientes" (audit).
_PRICING_RE = re.compile(
    r"\$\s*\d|"
    r"\b(cuánto|cuanto)\s+(cuesta|vale|sale|es|sería)\b|"
    r"\b(precio|precios|tarifa|tarifas|planes|costo|costos)\b|"
    r"\bcuesta\s+un\b|"
    r"\bcuánto\s+por\b|"
    r"\bqué\s+valor\b",
    re.IGNORECASE,
)

# --- Smalltalk: saludos y cortesías. ---
_SMALLTALK = ("hola", "buenos", "buenas", "gracias", "adiós", "adios", "chao")

# --- Discovery: el cliente cuenta su situación (incluye el dolor "me cuesta"). ---
_DISCOVERY = (
    "necesito", "busco", "quiero", "estoy buscando",
    "me interesa", "me gustaría",
    "me cuesta", "tengo un problema", "tengo una", "tengo un",
    "mi negocio", "mi empresa", "mi tienda", "mi local",
    "mi consultorio", "mi clínica", "mi despacho", "mi restaurante",
    # Del classifier anterior (no regresionar el enrutamiento viejo).
    "recomienda", "recomendación", "opciones", "producto", "tienen",
)

# Destinos con tools: objeción y comparación necesitan catálogo/knowledge
# para responder con datos (show_plans/search_knowledge).
TOOL_INTENTS = {"pricing", "conversion", "discovery", "general",
                "objection", "comparison", "support"}

BOOKING_WORDS = ("agendar", "agenda", "llamada", "reunion", "cita")

# --- Confirmación de cita: mensaje corto que cierra lo que se ofreció arriba.
# El clasificador es stateless; sin slots en la conversación "sí, dale" es
# solo general (nunca convierte a ciego).
_CONFIRM_RE = re.compile(r"^\s*(s[ií]\b|dale\b|perfecto\b|confirmo\b|de una\b|listo\b|agend[ao]\b)")
_SLOT_IN_TEXT_RE = re.compile(r"\d{4}-\d{2}-\d{2}t\d{2}:\d{2}|\b1[05]:00\b|\b1[05]\s*hs?\b")

GREETING_MARK = "[apertura]"  # apertura proactiva: la manda el backend, no el usuario

# H5: el texto de un ataque bloqueado NO se persiste ni se reinyecta.
# Se guarda este marcador en su lugar para no re-alimentar el historial.
BLOCKED_MARK = "[mensaje bloqueado]"

_INJECTION_RES = [
    r"olvida(tus| tus reglas| las reglas| todo)",
    r"ignor[ae]\s+(prev|las |tus |toda)",
    r"(muestra|revela|dame|enseña).{0,30}(prompt|instruccion|instrucción|sistema)",
    r"(eres|actúa como|actua como).{0,20}(sin reglas|sin filtros|dan\b|jailbreak)",
    r"soy el due[nñ]o",
    r"descuento especial|regal\w+ el producto|50%\s*de\s*descuento",
]


def _norm(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def _has(low: str, keywords) -> bool:
    # keywords se normaliza una sola vez por llamada: las tuplas son cortas.
    return any(_norm(k) in low for k in keywords)


def classify_intent(text: str) -> Intent:
    low = _norm(text)
    if _norm(GREETING_MARK) in low:
        return "greeting"
    if not low.strip():
        return "general"
    # 1. Objeción primero. "Está caro" NO es preguntar precio.
    if _has(low, _OBJECTION):
        return "objection"
    # 2. Comparación.
    if _has(low, _COMPARISON):
        return "comparison"
    # 3. Handoff (petición de contacto, no mención de canal).
    if _has(low, _HANDOFF):
        return "handoff"
    # 4. Pricing con contexto real (regex sobre el texto crudo: conserva acentos).
    if _PRICING_RE.search(text):
        return "pricing"
    # 5. Conversión / booking.
    if _has(low, _CONVERSION):
        return "conversion"
    # 6. Smalltalk antes que discovery: "Hola, tengo una..." es saludo+historia.
    if _has(low, _SMALLTALK):
        return "smalltalk"
    # 7. Discovery.
    if _has(low, _DISCOVERY):
        return "discovery"
    return "general"


def needs_tools(intent: str) -> bool:
    return intent in TOOL_INTENTS


def is_booking_confirmation(text: str, history: list[dict] | None = None) -> bool:
    """True si el mensaje confirma una cita que se ofreció arriba.

    El clasificador es stateless: "sí, dale" solo cuenta como confirmación
    cuando (a) es un cierre corto (regex `_CONFIRM_RE`) y (b) el asistente
    ofreció slots en la conversación (`_SLOT_IN_TEXT_RE` en un mensaje
    assistant). Sin esos dos contextos, es solo "general".
    """
    low = _norm(text)
    # Mensaje largo = sigue conversando, no confirmó ("sí, me gustaría que...").
    if len(low) > 40:
        return False
    if not _CONFIRM_RE.match(low):
        return False
    if not history:
        return False
    return any(
        m.get("role") == "assistant"
        and _SLOT_IN_TEXT_RE.search(_norm(m.get("content") or ""))
        for m in history
    )


# Días de la semana para elegir el slot que pidió el cliente.
_WEEKDAYS = {
    "lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3,
    "viernes": 4, "sabado": 5, "domingo": 6,
}


def pick_slot(text: str, slots: list[str]) -> str | None:
    """Elige el slot ofrecido que pide el cliente, o el primero si no explicita.

    Filtra por día ("el martes") y/o hora ("15", "15:00", "15 hs") cuando el
    mensaje los menciona; si no hay match, devuelve el primer slot libre.
    """
    if not slots:
        return None
    low = _norm(text)
    candidates = slots
    day = next((d for name, d in _WEEKDAYS.items() if name in low), None)
    if day is not None:
        matched = [s for s in candidates if datetime.fromisoformat(s).weekday() == day]
        if matched:
            candidates = matched
    hour = re.search(r"\b(1[05])(?:[:.]00|\s*hs)?\b", low)
    if hour:
        matched = [s for s in candidates if datetime.fromisoformat(s).hour == int(hour.group(1))]
        if matched:
            candidates = matched
    return candidates[0]


def is_injection(text: str) -> bool:
    low = _norm(text)
    return any(re.search(p, low) for p in _INJECTION_RES)
