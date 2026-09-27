"""Búsqueda externa segura (spec §14): fuentes abiertas sin API key.

Toda respuesta que salga de aquí es información externa y debe llevar el
disclaimer de "no oficial"; la consulta sensible (pagos, datos internos,
datos personales de terceros) ni siquiera llega a la red.
"""
import html
import re
import unicodedata
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from app.db.models import ExternalSearch

DISCLAIMER = "Información externa de fuentes públicas; no es una afirmación oficial de la empresa."

# --- Seguridad de la consulta (§14) ----------------------------------------


def _norm(text: str) -> str:
    """Minúsculas, sin acentos/ñ y con espacios colapsados."""
    stripped = "".join(
        c for c in unicodedata.normalize("NFKD", (text or "").lower()) if not unicodedata.combining(c)
    )
    return " ".join(stripped.split())


# Términos que marcan información interna/de pago: nunca sale de la empresa.
_BLOCKED_TERMS = (
    "pago",
    "factura",
    "transferencia",
    "credencial",
    "api key",
    "apikey",
    "token",
    "contrasena",
    "contrato",
    "confidencial",
    "privad",  # privado/privada
    "otro cliente",
    "otra cliente",
    "prompt",  # cubre "mi prompt", "qué prompt usás", etc.
    "instrucciones internas",
    "configuracion",
)

# Pidida de datos personales de terceros (sobre texto ya normalizado).
_THIRD_PARTY_RE = re.compile(
    r"datos personales|"
    r"(?:telefono|email|correo|direccion|domicilio|dni) (?:de|del) "
    r"(?:otro|otra|un|una|tercer|tercera) (?:cliente|persona|usuario)"
)


def is_safe_external(query: str) -> tuple[bool, str]:
    """True solo si la consulta no filtra info interna ni pide datos de terceros."""
    text = _norm(query)
    for term in _BLOCKED_TERMS:
        if term in text:
            return False, f"Contiene '{term}': es información interna o de pago, no sale de la empresa."
    if _THIRD_PARTY_RE.search(text):
        return False, "Pide datos personales de terceros."
    return True, ""


# --- Fuentes abiertas (sin API key) -----------------------------------------


def _strip_tags(raw: str) -> str:
    return html.unescape(re.sub(r"<[^>]*>", "", raw or "")).strip()


class WikipediaProvider:
    """API de búsqueda de Wikipedia en español (abierta, sin key)."""

    source = "wikipedia"
    URL = "https://es.wikipedia.org/w/api.php"

    def fetch(self, client: httpx.Client, query: str, k: int) -> list[dict]:
        r = client.get(
            self.URL,
            params={"action": "query", "list": "search", "format": "json", "srsearch": query, "srlimit": k},
        )
        r.raise_for_status()
        hits = ((r.json() or {}).get("query") or {}).get("search") or []
        out: list[dict] = []
        for hit in hits:
            if len(out) >= k:
                break
            title = (hit.get("title") or "").strip()
            if not title:
                continue
            out.append(
                {
                    "title": title,
                    "snippet": _strip_tags(hit.get("snippet") or ""),
                    "url": "https://es.wikipedia.org/wiki/" + quote(title.replace(" ", "_"), safe=""),
                    "source": self.source,
                }
            )
        return out


class DuckDuckGoProvider:
    """Instant Answer de DuckDuckGo (abierta, sin key): AbstractText + RelatedTopics."""

    source = "duckduckgo"
    URL = "https://api.duckduckgo.com/"

    @staticmethod
    def _topic(item: dict) -> dict | None:
        text = (item.get("Text") or "").strip()
        if not text:
            return None
        title, sep, snippet = text.partition(" - ")
        return {
            "title": title.strip(),
            "snippet": snippet.strip() if sep else "",
            "url": (item.get("FirstURL") or "").strip(),
            "source": DuckDuckGoProvider.source,
        }

    def fetch(self, client: httpx.Client, query: str, k: int) -> list[dict]:
        r = client.get(
            self.URL,
            params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
        )
        r.raise_for_status()
        data = r.json() or {}
        out: list[dict] = []
        abstract = (data.get("AbstractText") or "").strip()
        if abstract:
            out.append(
                {
                    "title": (data.get("Heading") or query).strip(),
                    "snippet": abstract,
                    "url": (data.get("AbstractURL") or "").strip(),
                    "source": self.source,
                }
            )
        for topic in data.get("RelatedTopics") or []:
            if len(out) >= k:
                break
            # RelatedTopics admite agrupaciones anidadas {"Topics": [...]}.
            for item in topic.get("Topics") or [topic]:
                if len(out) >= k:
                    break
                parsed = self._topic(item)
                if parsed:
                    out.append(parsed)
        return out[:k]


_PROVIDERS: tuple[WikipediaProvider | DuckDuckGoProvider, ...] = (WikipediaProvider(), DuckDuckGoProvider())


def search(query: str, k: int = 3, *, timeout: float = 8.0, transport: httpx.BaseTransport | None = None) -> list[dict]:
    """Prueba Wikipedia y luego DuckDuckGo; fusiona hasta k items.

    Nunca lanza excepción al caller: fuente caída -> se prueba la siguiente;
    sin ninguna fuente accesible -> [].
    """
    q = (query or "").strip()
    if not q or k <= 0:
        return []
    out: list[dict] = []
    try:
        with httpx.Client(timeout=timeout, transport=transport, follow_redirects=True) as client:
            for provider in _PROVIDERS:
                if len(out) >= k:
                    break
                try:
                    out.extend(provider.fetch(client, q, k - len(out)))
                except Exception:
                    pass  # fuente caída o respuesta inesperada: probamos la siguiente
    except Exception:
        pass  # nunca excepción al caller (spec §14)
    return out[:k]


# --- Forma canónica para el grafo -------------------------------------------


def search_external(
    db: Session,
    business_id: int,
    conversation_id: int,
    query: str,
    k: int = 3,
    *,
    timeout: float = 8.0,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    """Consulta segura + persistencia de auditoría (ExternalSearch) + disclaimer.

    Consulta insegura -> allowed=False y NO se persiste nada.
    """
    allowed, reason = is_safe_external(query)
    if not allowed:
        return {"allowed": False, "reason": reason, "results": []}
    results = search(query, k=k, timeout=timeout, transport=transport)
    row = ExternalSearch(
        business_id=business_id,
        conversation_id=conversation_id,
        query=query[:500],  # la columna String(500) es estricta en Postgres
        results=results,
        answer_used=False,
    )
    db.add(row)
    db.commit()
    return {
        "allowed": True,
        "results": results,
        "disclaimer": DISCLAIMER,
        "external_search_id": row.id,  # para mark_used cuando la info entre en la respuesta
    }


def mark_used(db: Session, external_search_id: int) -> None:
    """Marca que la info externa terminó en una respuesta (traza §14)."""
    row = db.get(ExternalSearch, external_search_id)
    if row is not None:
        row.answer_used = True
        db.commit()


def format_results(results: list[dict]) -> str:
    """Texto plano para inyectar al prompt, siempre con el disclaimer."""
    parts = []
    for i, r in enumerate(results or [], 1):
        parts.append(
            f"{i}. {(r.get('title') or '').strip()}\n"
            f"   {(r.get('snippet') or '').strip()}\n"
            f"   Fuente: {(r.get('source') or '').strip()} — {(r.get('url') or '').strip()}"
        )
    body = "\n".join(parts)
    return f"{body}\n\n{DISCLAIMER}" if body else DISCLAIMER
