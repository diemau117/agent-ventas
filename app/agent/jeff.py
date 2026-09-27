"""Jeff (spec §13): capa de decisión/análisis determinista, sin LLM.

Dado un ``context`` (intención clasificada, mensaje, historia, resultados de
tools, perfil del cliente y datos del negocio), Jeff decide el siguiente paso
de la conversación, si hace falta RAG, si hace falta una búsqueda externa y si
hay que escalar a un humano. Rápido, gratis y testeable.

Reglas en orden — la primera que matchea gana:

1. ``intent == "blocked"`` → ``ANSWER`` (el grafo ya emitió la refusal).
2. ``intent == "handoff"`` o el mensaje pide atención en humano/persona/
   agente/asesor/supervisor, o pide contacto por whatsapp/teléfono con verbo
   de contacto (hablar/llamar/escribir/contactar) → ``HANDOFF`` +
   ``escalate=True``. Una pregunta *informativa* del teléfono del negocio
   ("¿cuál es su teléfono?") no es petición de contacto: cae en la regla 8.
3. Señales de queja/tensión (queja, reclamo, no funciona, estafa, malo,
   terrible, decepcion) → ``HANDOFF`` + ``escalate=True``.
4. ``intent == "conversion"`` y ``tool_results["create_appointment"]`` con
   ``appointment_id`` → ``CLOSE`` (cita creada: toca cerrar).
5. ``intent == "conversion"`` sin cita → ``SCHEDULE``.
6. ``intent == "objection"`` → ``HANDLE_OBJECTION`` (reconocer, datos,
   reducir riesgo, proponer el siguiente paso).
7. ``intent == "comparison"`` → ``RECOMMEND`` con catálogo en
   ``tool_results``; sin catálogo → ``EDUCATE`` (valor antes que pitch).
8. ``intent == "pricing"`` → ``PRESENT_OFFER`` si el catálogo de
   ``tool_results`` trae productos con precio; si no, ``needs_rag=True`` y
   ``EDUCATE``.
9. ``intent == "discovery"`` → ``RECOMMEND`` si hay productos en
   ``tool_results``; si no, ``needs_rag=True`` y ``DISCOVER``.
10. Pregunta de horario/dirección/teléfono (regex sobre el mensaje normalizado)
    → ``ANSWER``; con ``needs_rag=True`` si el dato correspondiente
    (``business_hours`` / ``business_address`` / ``business_phone``) viene
    vacío en el context.
11. Pregunta que no responde el knowledge (``knowledge_hits == 0``) ni el
    catálogo, con ``external_search_enabled`` y que es **externa segura** (no
    menciona pago/factura/transferencia/contrato/confidencial/credencial/
    api key/contraseña/prompt, no pide datos de otro cliente ni prompt/config
    interna) → ``needs_external=True`` + ``ANSWER``.
12. Intención de compra/decisión ("quiero", "lo llevo", "cómo contrato",
    "cómo seguimos", "acepto") → ``CAPTURE_CONTACT`` si ``profile`` no tiene
    contacto (sin ``@`` ni dígitos de teléfono); ``CLOSE`` si ya hay.
13. ``FOLLOW_UP``: contacto en ``profile``, ``message_count >= 4``, **sin
    cita previa** (``has_appointment`` en el context) e intención
    general/smalltalk/greeting → el lead está tibio y estancado: retomar el
    hilo y concretar en vez de seguir descubriendo.
14. ``intent == "smalltalk"`` → ``CLARIFY`` si ``message_count < 2``, si no
    ``DISCOVER``.
15. Default → ``DISCOVER``.

``HANDLE_OBJECTION`` y ``FOLLOW_UP`` los emiten las reglas 6 y 13 (antes
quedaban reservados sin emisor). Jeff no reimplementa ``needs_tools``: esa
decisión vive en ``app.agent.policies`` (única fuente).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.db.models import LEAD_TEMPERATURES

__all__ = [
    "Decision",
    "NEXT_STEPS",
    "decide",
    "temperature_hint",
    "should_use_rag",
    "should_escalate",
]

# Vocabulario exacto de next_step (spec §13).
NEXT_STEPS = (
    "DISCOVER",
    "CLARIFY",
    "EDUCATE",
    "RECOMMEND",
    "PRESENT_OFFER",
    "HANDLE_OBJECTION",
    "CLOSE",
    "CAPTURE_CONTACT",
    "SCHEDULE",
    "FOLLOW_UP",
    "HANDOFF",
    "ANSWER",
)


@dataclass
class Decision:
    """Decisión de Jeff para un turno de conversación."""

    next_step: str = "DISCOVER"
    needs_rag: bool = False
    needs_external: bool = False
    escalate: bool = False
    escalate_reason: str = ""
    lead_temperature_hint: str = "frio"
    reason: str = ""


# --- normalización -----------------------------------------------------------

def _norm(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def _text(value) -> str:
    return value if isinstance(value, str) else ""


# --- reglas 2-3: humano y quejas --------------------------------------------

_CONTACT_VERBS = (
    "hablar", "habla", "hablo", "hablas",
    # "llamo" (yo llamo / "me llamo Pedro") NO es petición de contacto:
    # era el bug del audit P1 — dar datos personales disparaba handoff.
    "llamar", "llamada", "llamame", "llamas",
    "escribir", "escribime", "escribirme", "escribe",
    "contactar", "contacto", "comunicar", "comunica",
    "derivar", "escalar", "atender", "atencion",
)
_HUMAN_TOKENS = ("humano", "humana", "asesor", "asesora", "supervisor", "agente", "encargad", "jefe")
_CHANNEL_TOKENS = ("whatsapp", "telefono", "celular")
_COMPLAINT_SIGNALS = ("queja", "reclam", "no funciona", "estafa", "malo", "mala", "terrible", "decepcion")


def _human_request(low: str) -> str | None:
    """Motivo concreto si el mensaje pide atención/contacto humano."""
    if any(t in low for t in _HUMAN_TOKENS):
        return "el usuario pide atención de una persona (humano/asesor/agente)"
    has_verb = any(v in low for v in _CONTACT_VERBS)
    if has_verb and "persona" in low:
        return "el usuario pide hablar con una persona"
    if has_verb and any(c in low for c in _CHANNEL_TOKENS):
        return "el usuario pide contacto por whatsapp/teléfono"
    return None


def _complaint_signal(low: str) -> str | None:
    return next((s for s in _COMPLAINT_SIGNALS if s in low), None)


# --- regla 4-7: catálogo -----------------------------------------------------

def _catalog(tool_results: dict) -> list[dict]:
    out: list[dict] = []
    for value in tool_results.values():
        if isinstance(value, dict):
            items = value.get("products")
            if isinstance(items, list):
                out.extend(i for i in items if isinstance(i, dict))
    return out


def _has_products(tool_results: dict) -> bool:
    return bool(_catalog(tool_results))


def _has_priced_products(tool_results: dict) -> bool:
    return any(p.get("price_cents") is not None for p in _catalog(tool_results))


def _appointment_created(tool_results: dict) -> bool:
    appt = tool_results.get("create_appointment")
    return isinstance(appt, dict) and appt.get("appointment_id") is not None


def _lead_created(tool_results: dict) -> bool:
    lead = tool_results.get("create_lead")
    return isinstance(lead, dict) and lead.get("lead_id") is not None


# --- regla 8: datos del negocio ---------------------------------------------

_HOURS_RE = re.compile(
    r"(\bhorario|\babren\b|\bcierran\b|\babre\b|\bcierra\b|\babierto\b|\bcerrado\b"
    r"|a\s?que\s?hora|hora\s?de\s?(apertura|cierre|atencion))"
)
_ADDRESS_RE = re.compile(
    r"(\bdireccion\b|\bubicacion\b|\bdomicilio\b"
    r"|donde\s+(esta|estan|queda|quedan|voy|llego|encuentr)"
    r"|a\s?donde\s+(voy|ir|llego)|como\s+llego)"
)
_PHONE_RE = re.compile(
    r"(\btelefono|\bcelular\b|\bwhatsapp\b|numero\s?de\s?(telefono|contacto|celular)|linea\s?directa)"
)

_DATA_TOPICS = (
    ("horario", _HOURS_RE, "business_hours"),
    ("direccion", _ADDRESS_RE, "business_address"),
    ("telefono", _PHONE_RE, "business_phone"),
)

_Q_START = re.compile(r"(que|como|donde|cuando|cual|quien|cuanto|a que|por que)")
_Q_ANY = re.compile(
    r"(queria|sabes|sabe|pueden|puede|podrias|podes|puedes|dime|decime|informame|pasame|me pasas|necesito saber)"
)


def _is_question(low: str) -> bool:
    if "?" in low or "¿" in low:
        return True
    return bool(_Q_ANY.search(low) or _Q_START.match(low))


def _present(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return bool(value)


# --- regla 9: externa segura -------------------------------------------------

_UNSAFE_TERMS = (
    "pago", "paga", "factura", "transferencia", "contrato", "confidencial",
    "credencial", "api key", "apikey", "contrasena", "password", "prompt",
)
_UNSAFE_RES = (
    r"(otro|otra|otros|otras)\s+(cliente|usuario|cuenta|persona)",
    r"datos\s+de\s+(otro|otra|otros|otras)",
    r"(configuracion|config|instrucciones?|habilitaciones?)\s+(interna|interno|del sistema|del bot|del agente|oculta)",
    r"(interna|interno)\b.{0,20}\b(configuracion|config|instrucciones?|datos)\b",
)


def _external_safe(low: str) -> bool:
    if any(term in low for term in _UNSAFE_TERMS):
        return False
    return not any(re.search(pattern, low) for pattern in _UNSAFE_RES)


# --- regla 10: compra y contacto ---------------------------------------------

_PURCHASE_PHRASES = ("quiero", "lo llevo", "como contrato", "como seguimos", "acepto")

_PHONE_LIKE_RE = re.compile(r"\d[\d\s().-]{5,}\d")


def _flatten(value) -> str:
    if isinstance(value, dict):
        return " ".join(_flatten(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten(v) for v in value)
    return str(value)


def _has_contact(profile) -> bool:
    """Hay contacto en el perfil: ``@`` (email) o dígitos de teléfono."""
    if not profile:
        return False
    text = _norm(_flatten(profile))
    if "@" in text:
        return True
    return bool(_PHONE_LIKE_RE.search(text))


def _message_count(ctx: dict) -> int:
    mc = ctx.get("message_count")
    if isinstance(mc, int) and not isinstance(mc, bool):
        return max(mc, 0)
    history = ctx.get("history")
    return len(history) if isinstance(history, list) else 0


def _knowledge_hits(ctx: dict) -> int:
    kh = ctx.get("knowledge_hits")
    return kh if isinstance(kh, int) and not isinstance(kh, bool) else 0


# --- API pública -------------------------------------------------------------

def decide(context: dict) -> Decision:
    """Aplica las reglas §13 en orden (primera que matchea gana).

    ``context`` acepta claves opcionales: ``intent``, ``user_message``,
    ``history``, ``tool_results``, ``profile``, ``knowledge_hits``,
    ``business_hours``, ``business_address``, ``business_phone``,
    ``message_count``, ``external_search_enabled``, ``has_external_answer``.
    Un context vacío devuelve ``Decision`` con defaults razonables
    (``next_step="DISCOVER"``, sin flags), sin excepción. El orden completo de
    reglas está documentado en el docstring del módulo.
    """
    ctx = context or {}
    intent = _text(ctx.get("intent"))
    low = _norm(_text(ctx.get("user_message")))
    raw_tools = ctx.get("tool_results")
    tools = raw_tools if isinstance(raw_tools, dict) else {}
    hint = temperature_hint(ctx)

    def mk(step: str, reason: str, **flags) -> Decision:
        if step not in NEXT_STEPS:
            raise ValueError(f"next_step fuera del vocabulario §13: {step}")
        if flags.get("escalate") and not flags.get("escalate_reason"):
            raise ValueError("escalate=True requiere escalate_reason")
        return Decision(next_step=step, reason=reason, lead_temperature_hint=hint, **flags)

    # 1) bloqueo de inyección: el grafo ya clasificó y va a negar.
    if intent == "blocked":
        return mk("ANSWER", "bloqueo de inyección: el grafo ya maneja la refusal")

    # 2) handoff: intención clasificada o petición concreta de contacto humano.
    human = _human_request(low)
    if intent == "handoff" or human:
        reason = human or "intención handoff clasificada por el grafo"
        return mk("HANDOFF", f"handoff: {reason}", escalate=True, escalate_reason=reason)

    # 3) queja/tensión: escalar antes de empeorar.
    signal = _complaint_signal(low)
    if signal:
        return mk(
            "HANDOFF",
            "señales de queja/tensión: conversación humana",
            escalate=True,
            escalate_reason=f"queja detectada en el mensaje: {signal}",
        )

    # 4) conversión con cita ya creada → cerrar.
    if intent == "conversion" and _appointment_created(tools):
        return mk("CLOSE", "cita creada (appointment_id): toca cerrar")

    # 5) conversión sin cita → agendar.
    if intent == "conversion":
        return mk("SCHEDULE", "intención de conversión sin cita: agendar")

    # 6) objeción de venta: no discutir; reconocer, datos y siguiente paso.
    if intent == "objection":
        return mk("HANDLE_OBJECTION", "objeción de venta clasificada: manejar sin discutir")

    # 7) comparación: recomendar con catálogo; sin catálogo, educar en valor.
    if intent == "comparison":
        if _has_products(tools):
            return mk("RECOMMEND", "comparación con catálogo: recomendar opción propia")
        return mk("EDUCATE", "comparación sin catálogo: educar en valor antes que pitch")

    # 8) pricing: oferta si hay precios, si no RAG para educar.
    if intent == "pricing":
        if _has_priced_products(tools):
            return mk("PRESENT_OFFER", "catálogo con precios disponible: presentar la oferta")
        return mk("EDUCATE", "pricing sin catálogo con precios: RAG antes de educar", needs_rag=True)

    # 7) discovery: recomendar si hay catálogo, si no RAG + descubrir.
    if intent == "discovery":
        if _has_products(tools):
            return mk("RECOMMEND", "catálogo disponible: recomendar opción")
        return mk("DISCOVER", "discovery sin catálogo: RAG + descubrir la necesidad", needs_rag=True)

    # 8) datos del negocio: responder del context o pedir RAG.
    if _is_question(low):
        for topic, pattern, key in _DATA_TOPICS:
            if pattern.search(low):
                if _present(ctx.get(key)):
                    return mk("ANSWER", f"pregunta de {topic}: el dato está en el context → respuesta directa")
                return mk(
                    "ANSWER",
                    f"pregunta de {topic}: falta el dato en el context → RAG antes de responder",
                    needs_rag=True,
                )

    # 9) pregunta externa segura: knowledge y catálogo no la responden.
    if (
        _is_question(low)
        and _knowledge_hits(ctx) == 0
        and not _catalog(tools)
        and ctx.get("external_search_enabled")
        and _external_safe(low)
    ):
        return mk("ANSWER", "pregunta externa segura sin knowledge ni catálogo: buscar info externa", needs_external=True)

    # 10) intención de compra/decisión: cerrar o capturar contacto.
    if any(phrase in low for phrase in _PURCHASE_PHRASES):
        if _has_contact(ctx.get("profile")):
            return mk("CLOSE", "intención de compra con contacto en profile: cerrar")
        return mk("CAPTURE_CONTACT", "intención de compra sin contacto en profile: capturar contacto")

    # 11) lead tibio estancado: retomar el hilo y concretar (FOLLOW_UP).
    if (
        intent in ("general", "smalltalk", "greeting")
        and _has_contact(ctx.get("profile"))
        and _message_count(ctx) >= 4
        and not (ctx.get("has_appointment") or _appointment_created(tools))
    ):
        return mk(
            "FOLLOW_UP",
            f"contacto en profile, message_count={_message_count(ctx)}, sin cita: retomar y concretar",
        )

    # 12) smalltalk: clarificar temprano, descubrir después.
    if intent == "smalltalk":
        mc = _message_count(ctx)
        if mc < 2:
            return mk("CLARIFY", f"smalltalk temprano (message_count={mc}): aclarar la intención real")
        return mk("DISCOVER", f"smalltalk avanzado (message_count={mc}): descubrir la necesidad")

    # 12) default.
    return mk("DISCOVER", "sin regla específica (§13): descubrir la necesidad")


def temperature_hint(context: dict) -> str:
    """Hint de temperatura del lead con reglas simples de Jeff:

    contacto en el profile o lead creado → ``caliente``; problema o precio en
    el mensaje → ``caliente``; ``message_count >= 4`` → ``tibio``; default →
    ``frio``. El valor se valida contra ``LEAD_TEMPERATURES``.
    """
    ctx = context or {}
    msg = _norm(_text(ctx.get("user_message")))
    raw_tools = ctx.get("tool_results")
    tools = raw_tools if isinstance(raw_tools, dict) else {}
    if _has_contact(ctx.get("profile")) or _lead_created(tools):
        hint = "caliente"
    elif re.search(r"(problema|no funciona|urgente|precio|cuesta|costo)", msg):
        hint = "caliente"
    elif _message_count(ctx) >= 4:
        hint = "tibio"
    else:
        hint = "frio"
    if hint not in LEAD_TEMPERATURES:
        raise ValueError(f"temperatura fuera de LEAD_TEMPERATURES: {hint}")
    return hint


def should_use_rag(context: dict) -> bool:
    """True si Jeff decide que el turno necesita RAG."""
    return decide(context).needs_rag


def should_escalate(context: dict) -> bool:
    """True si Jeff decide escalar a un humano."""
    return decide(context).escalate
