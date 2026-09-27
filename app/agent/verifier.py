"""Verificador determinista: ningún claim comercial sin respaldo exacto."""
import re
import unicodedata
from datetime import datetime

# Montos reales ($/moneda). "100%" no es un monto — por eso H9 estaba roto.
_AMOUNT_ONLY_RE = re.compile(r"\$\s?[\d.,]+|\d+\s?(usd|mxn|pesos|€)")
_AMOUNT_RE = re.compile(r"\$\s?([\d.,]+)|([\d.,]+)\s?(?:usd|mxn|pesos|€)")
_STOCK_RE = re.compile(r"disponib|en stock|tenemos|sí (ten|hay)|si (ten|hay)")
_APPT_RE = re.compile(r"agendad[ao]|\bagende\b|confirmad[ao]|anotad[ao]|\banote\b")

# H9: "%"-suelo no es un descuento ("100% arábica"). Solo cuenta como oferta
# cuando el porcentaje o la palabra vienen con contexto promocional explícito.
_PROMO_WORD_RE = re.compile(r"descuento|descuent|promoci|promoc|oferta|rebaj|\bdto\b|\bofertas\b")
_PCT_PROMO_RE = re.compile(r"\d{1,3}\s*%\s*(?:de\s+|en\s+)?(?:descuento|dto|rebaja|off)")

# H2: hechos no-precio que exigen respaldo de knowledge en este turno.
_HOURS_RE = re.compile(
    r"horario|abrimos|cierran|abierto|de lunes|lunes a|todos los d[íi]as|domingos?|s[áa]bados?"
)
_ADDRESS_RE = re.compile(
    r"direcci[óo]n|estamos en|ubicad|nos encontramos|calle|avenida|local en|sucursal"
)

# Negación corta: "no tenemos descuento" es una negación, no una promesa.
_NEGATION_RE = re.compile(
    r"\b(?:no|sin|nunca|jam[áa]s|ningun[óoa]|tampoco|no tenemos|no hay|no contamos|no ofrecemos)\b"
)


def _is_denial(low: str, start: int) -> bool:
    """True si antes del match hay una negación corta (~40 chars)."""
    window = low[max(0, start - 40) : start]
    return bool(_NEGATION_RE.search(window))


def _has_promo_claim(low: str) -> tuple[bool, bool]:
    """(hay oferta afirmada, hay % en contexto promocional).

    Separa la oferta afirmada del % decorativo y de las negaciones.
    """
    word_claim = False
    for m in _PROMO_WORD_RE.finditer(low):
        if not _is_denial(low, m.start()):
            word_claim = True
            break
    pct_claim = False
    for m in _PCT_PROMO_RE.finditer(low):
        if not _is_denial(low, m.start()):
            pct_claim = True
            break
    return word_claim, pct_claim


def _knowledge_entries(tool_results: dict) -> list[dict]:
    """Entradas de knowledge que llegaron en este turno (spec §3-4 / RAG)."""
    out: list[dict] = []
    for key in ("search_knowledge", "knowledge"):
        block = (tool_results or {}).get(key) or {}
        if isinstance(block, dict):
            items = block.get("entries") or block.get("documents") or []
            if isinstance(items, list):
                out.extend(items)
    # También sirven las categorías sueltas que inyecta el grafo (horario/direccion).
    for key in ("business_hours", "business_address"):
        val = (tool_results or {}).get(key)
        if val:
            out.append({"category": "horario" if "hours" in key else "direccion", "content": str(val)})
    return out


def _has_knowledge_support(entries: list[dict], category: str, draft: str) -> bool:
    """¿El claim del draft está respaldado por knowledge de esa categoría?

    Exige solapamiento real con el contenido (no basta con que exista un doc
    de la categoría): si el asistente inventa un horario distinto al real,
    los tokens del doc no aparecen y se rechaza.
    """
    low = (draft or "").lower()
    for e in entries:
        if (e or {}).get("category") != category:
            continue
        content = str((e or {}).get("content") or "")
        # Trocear por no-alfanuméricos: "Lun-Vie 9-17" → lun, vie, 9, 17.
        # Con split() por espacios el overlap no funcionaba.
        for token in re.split(r"[^0-9a-zA-ZáéíóúñüÁÉÍÓÚÑÜ]+", content):
            t = token.strip().lower()
            if len(t) >= 3 and t in low:
                return True
    return False


def _norm(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", (text or "").lower()) if not unicodedata.combining(c))


def _catalogs(tool_results: dict) -> list[dict]:
    out = []
    for key in ("search_products", "show_plans"):
        items = ((tool_results or {}).get(key) or {}).get("products") or []
        out.extend(items)
    return out


def _has_products(tool_results: dict) -> bool:
    return bool(_catalogs(tool_results))


def _has_availability(tool_results: dict) -> bool:
    """Evidencia de disponibilidad: catálogo con stock O slots de agenda.

    En un bot de citas, `check_availability` ES la fuente de "tenemos
    horarios disponibles" — mirar solo productos rechazaba drafts legítimos.
    """
    if _has_products(tool_results):
        return True
    return bool(((tool_results or {}).get("check_availability") or {}).get("slots"))


_WEEKDAY_ES = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")


def _slot_supports(tool_results: dict, low: str) -> bool:
    """True si el día/hora mencionado en el draft coincide con un slot ofrecido.

    Cubre el claim "el lunes a las 10" cuando ese slot viene de
    `check_availability` (no del knowledge de horarios del negocio).
    """
    for raw in ((tool_results or {}).get("check_availability") or {}).get("slots") or []:
        try:
            slot = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            continue
        day = _WEEKDAY_ES[slot.weekday()]
        hour = f"{slot.hour:02d}"
        if day in low and (hour in low or f"{slot.hour} hs" in low):
            return True
    return False


def _prices(tool_results: dict) -> list[float]:
    return [p["price_cents"] / 100 for p in _catalogs(tool_results) if p.get("price_cents") is not None]


def _parse_amount(raw: str) -> float | None:
    s = raw.strip()
    try:
        if "." in s and "," in s:  # 1.299,00 | 1,299.00: el último separador es decimal
            s = s.replace(".", "#").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
            s = s.replace("#", "")
        elif "," in s:
            s = s.replace(",", ".")
        return float(s)
    except ValueError:
        return None


def _amounts_match(draft: str, prices: list[float]) -> bool:
    """Todo monto con $/moneda debe coincidir con algún precio autorizado."""
    for m in _AMOUNT_RE.finditer(draft):
        raw = m.group(1) or m.group(2)
        val = _parse_amount(raw or "")
        if val is None:
            continue
        if not any(abs(val - p) < 0.005 for p in prices):
            return False
    return True


def verify_response(draft: str, tool_results: dict) -> dict:
    issues, unsupported = [], []
    low = (draft or "").lower()
    entries = _knowledge_entries(tool_results)

    # --- Dinero y promociones (H1 + H9) ---
    # Entra si hay un monto real ($/moneda) O un claim promo no negado.
    # "100% arábica" no trae monto ni promo → no entra (H9).
    # "No tenemos ningún descuento" trae promo pero negada → no entra (H9).
    word_claim, pct_claim = _has_promo_claim(low)
    if _AMOUNT_ONLY_RE.search(low) or word_claim or pct_claim:
        if word_claim or pct_claim:
            issues.append("discount_without_source")
            unsupported.append("descuento/promoción sin fuente autorizada")
        elif not _prices(tool_results):
            issues.append("price_without_evidence")
            unsupported.append("precio sin respaldo en tool_results")
        elif not _amounts_match(draft, _prices(tool_results)):
            issues.append("price_mismatch")
            unsupported.append("monto no coincide con ningún precio autorizado")

    # --- Disponibilidad (H1) ---
    if _STOCK_RE.search(low) and not _has_availability(tool_results):
        if not _is_denial(low, _STOCK_RE.search(low).start()):
            issues.append("availability_without_evidence")
            unsupported.append("disponibilidad sin respaldo en tool_results")

    # --- Citas (SalesMind) ---
    if _APPT_RE.search(_norm(draft)):
        appt = (tool_results or {}).get("create_appointment") or {}
        if not appt.get("appointment_id"):
            appt_norm = _norm(draft)
            neg = _is_denial(appt_norm, _APPT_RE.search(appt_norm).start())
            if not neg:
                issues.append("appointment_unconfirmed")
                unsupported.append("cita afirmada sin confirmación del backend")

    # --- H2: hechos no-precio exigen knowledge de este turno ---
    m_hours = _HOURS_RE.search(low)
    if m_hours and not _is_denial(low, m_hours.start()):
        if not _has_knowledge_support(entries, "horario", draft or "") and not _slot_supports(
            tool_results, low
        ):
            issues.append("hours_without_evidence")
            unsupported.append("horario afirmado sin respaldo en knowledge")
    m_addr = _ADDRESS_RE.search(low)
    if m_addr and not _is_denial(low, m_addr.start()):
        if not _has_knowledge_support(entries, "direccion", draft or ""):
            issues.append("address_without_evidence")
            unsupported.append("dirección afirmada sin respaldo en knowledge")

    return {"approved": not issues, "issues": issues, "unsupported": unsupported}
