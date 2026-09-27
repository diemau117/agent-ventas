import logging
import time

import httpx
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.agent.prompts import FALLBACK_ERROR
from app.config import settings
from app.db.database import get_db
from app.llm.base import FakeProvider, LLMProvider
from app.llm.groq import GroqProvider
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.conversation import run_chat

router = APIRouter()
log = logging.getLogger("chat")
_llm: LLMProvider | None = None

# El rate limit por IP vive en RateLimitMiddleware (app/middleware.py),
# persistido en RateLimitBucket y correcto con múltiples workers.

CAPTURE_QUOTA = (
    "Estoy con muchísimo trabajo ahora mismo y no quiero atenderte a las apuradas. "
    "Dejame tu WhatsApp acá y te escribo en un rato para seguir, ¿te parece?"
)


def get_llm() -> LLMProvider:
    global _llm
    if _llm is None:
        _llm = GroqProvider(settings.groq_api_key, settings.llm_model) if settings.groq_api_key else FakeProvider()
    return _llm


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request, db: Session = Depends(get_db)):
    if not req.greeting and not req.message.strip():
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="message_required")

    # Auth: el tenant lo resuelve el servidor desde la clave pública.
    # Sin esto cualquier visitante podría chatear como cualquier business_id.
    from app.api.deps import PUBLIC_KEY_HEADER, business_by_key, enforce_budget

    business = business_by_key(
        db, req.public_key or (request.headers.get(PUBLIC_KEY_HEADER) or "")
    )
    enforce_budget(db, business)

    from app.observability import log_turn, new_request_id

    rid = new_request_id()
    started = time.monotonic()
    try:
        out = await run_chat(
            db, get_llm(), business.id, req.conversation_id, req.message,
            greeting=req.greeting, advisor=req.advisor,
        )
        log_turn(
            db,
            request_id=rid,
            business_id=business.id,
            conversation_id=out.get("conversation_id"),
            intent=out.get("intent", ""),
            decision=(out.get("decision") or {}).get("next_step", ""),
            issues=out.get("issues") or [],
            tools=out.get("tools") or {},
            tokens_in=out.get("tokens_in", 0),
            tokens_out=out.get("tokens_out", 0),
            latency_ms=int((time.monotonic() - started) * 1000),
            message="turn_ok",
        )
        return ChatResponse(
            **{k: v for k, v in out.items()
               if k in ChatResponse.model_fields},
            next_step=(out.get("decision") or {}).get("next_step", ""),
        )
    except LookupError:
        # H11: un solo mensaje, no distinguir negocio de conversación.
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="not_found")
    except httpx.HTTPStatusError as e:
        # Cuota del LLM agotada: capturar el contacto en vez de perder la venta.
        log.warning("chat_quota_exhausted: %s", e.response.status_code)
        if e.response.status_code == 429:
            return ChatResponse(conversation_id=req.conversation_id or 0, reply=CAPTURE_QUOTA)
        log.exception("chat_failed")
        return ChatResponse(conversation_id=req.conversation_id or 0, reply=FALLBACK_ERROR)
    except Exception:
        log.exception("chat_failed")
        return ChatResponse(conversation_id=req.conversation_id or 0, reply=FALLBACK_ERROR)
