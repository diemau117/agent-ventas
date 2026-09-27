"""Retrieval léxico determinista sobre Knowledge (spec §3-4).

Sin embeddings ni dependencias nuevas: normalización + solapamiento de tokens.
"""
import re
import unicodedata

from app.db.models import Knowledge


def _norm(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", _norm(text)) if t]


def retrieve(db, business_id: int, query: str, k: int = 4) -> list[dict]:
    """Devuelve los top-k docs activos del negocio ordenados por relevancia.

    Score = solapamiento de tokens de la query contra title (x2), keywords y
    content. Docs con score 0 se descartan.
    """
    query_tokens = set(_tokens(query))
    if not query_tokens or k <= 0:
        return []
    docs = (
        db.query(Knowledge)
        .filter(Knowledge.business_id == business_id, Knowledge.active.is_(True))
        .all()
    )
    scored = []
    for doc in docs:
        title_tokens = set(_tokens(doc.title))
        kw_tokens = set(_tokens(doc.keywords or ""))
        content_tokens = set(_tokens(doc.content or ""))
        score = 0.0
        for tok in query_tokens:
            if tok in title_tokens:
                score += 2.0
            elif tok in kw_tokens or tok in content_tokens:
                score += 1.0
        if score > 0:
            scored.append(
                {
                    "id": doc.id,
                    "title": doc.title,
                    "content": doc.content,
                    "category": doc.category,
                    "score": score,
                }
            )
    scored.sort(key=lambda item: (-item["score"], item["id"]))
    return scored[:k]


def knowledge_context(db, business_id: int, query: str, k: int = 4) -> str:
    """Bloque de texto listo para inyectar al prompt. Sin matches → ""."""
    matches = retrieve(db, business_id, query, k)
    if not matches:
        return ""
    lines = ["Información de la empresa (fuente autorizada):"]
    for m in matches:
        lines.append(f"- [{m['category']}] {m['title']}: {m['content']}")
    return "\n".join(lines)


def categories_for(db, business_id: int, categories: tuple[str, ...]) -> list[dict]:
    """Docs activos de las categorías pedidas, sin importar query (verificador H2)."""
    if not categories:
        return []
    docs = (
        db.query(Knowledge)
        .filter(
            Knowledge.business_id == business_id,
            Knowledge.active.is_(True),
            Knowledge.category.in_(categories),
        )
        .order_by(Knowledge.id)
        .all()
    )
    return [
        {"id": d.id, "title": d.title, "content": d.content, "category": d.category}
        for d in docs
    ]
