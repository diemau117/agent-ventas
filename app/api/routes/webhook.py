"""Webhook de Chatwoot (spec §15): la respuesta del humano vuelve al widget.

Verificación (best-effort, documentada): Chatwoot NO envía credenciales en sus
webhooks por defecto y aquí no se inventa un secreto global nuevo. Si
`settings.chatwoot_token` está vacío, se acepta cualquier POST (best-effort).
Si está configurado, el request debe traer la misma credencial en el header
`api_access_token` o en el query param `?token=`; sin coincidencia → 401.
Para exigirlo en producción: setear CHATWOOT_TOKEN y usar en la suscripción de
Chatwoot la URL `.../api/webhook/chatwoot?token=<ese token>`.

Sender humano = ("user", "agent", "human"): se persiste el contenido como
Message(role="assistant") y la conversación pasa a state="human". Cualquier
otro sender (contact, agent_bot, system, ...), los mensajes que nosotros
empujamos (content_attributes.source == SOURCE_MARK) y eventos que no sean
message_created se ignoran sin persistir (evita bucle).

Correlación: el id de conversación puede venir como `conversation_id` de
top-level, en `conversation.additional_attributes.agent_ventas_conversation_id`
(lo ponemos al crear la conversación en Chatwoot) o como `conversation.id`
(fallback). Si no existe la conversación interna → 404 conversation_not_found.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_db
from app.db.models import Conversation, Message
from app.integrations.chatwoot import SOURCE_MARK

router = APIRouter()

# Tipos de sender considerados "humanos" en el payload de Chatwoot (spec §15).
HUMAN_SENDER_TYPES = ("user", "agent", "human")


def _verificar_token(request: Request) -> None:
    """Best-effort: solo exige credencial si settings.chatwoot_token está seteado."""
    esperado = (settings.chatwoot_token or "").strip()
    if not esperado:
        return
    recibido = request.headers.get("api_access_token") or request.query_params.get("token") or ""
    if recibido != esperado:
        raise HTTPException(status_code=401, detail="invalid_webhook_token")


def _ref_conversacion(data: dict) -> tuple[int | None, int | None]:
    """(id de conversación interna, business_id) que trae el payload; None si no se puede resolver."""
    ref = data.get("conversation_id")
    negocio = None
    if ref is None:
        conv_obj = data.get("conversation")
        conv_obj = conv_obj if isinstance(conv_obj, dict) else {}
        attrs = conv_obj.get("additional_attributes")
        attrs = attrs if isinstance(attrs, dict) else {}
        if attrs.get("agent_ventas_conversation_id") is not None:
            ref = attrs.get("agent_ventas_conversation_id")
            negocio = attrs.get("agent_ventas_business_id")
        else:
            ref = conv_obj.get("id", conv_obj.get("display_id"))
    try:
        ref = int(ref)
    except (TypeError, ValueError):
        ref = None
    try:
        negocio = int(negocio) if negocio is not None else None
    except (TypeError, ValueError):
        negocio = None
    return ref, negocio


@router.post("/webhook/chatwoot")
async def chatwoot_webhook(request: Request, db: Session = Depends(get_db)):
    """Evento de Chatwoot → persiste la respuesta del humano en la conversación interna."""
    _verificar_token(request)
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_payload")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="invalid_payload")

    event = data.get("event")
    if event is not None and event != "message_created":
        return {"ok": True, "ignored": True}

    attrs = data.get("content_attributes")
    attrs = attrs if isinstance(attrs, dict) else {}
    if attrs.get("source") == SOURCE_MARK:
        # Mensaje que nosotros empujamos a Chatwoot: no re-persistir (evita bucle).
        return {"ok": True, "ignored": True}

    sender = data.get("sender")
    sender = sender if isinstance(sender, dict) else {}
    sender_type = str(data.get("sender_type") or sender.get("type") or "").lower()
    if sender_type not in HUMAN_SENDER_TYPES:
        return {"ok": True, "ignored": True}

    conv_id, business_id = _ref_conversacion(data)
    if conv_id is None:
        raise HTTPException(status_code=404, detail="conversation_not_found")
    filtros = [Conversation.id == conv_id]
    if business_id is not None:
        filtros.append(Conversation.business_id == business_id)
    conv = db.query(Conversation).filter(*filtros).first()
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation_not_found")

    content = data.get("content")
    if not isinstance(content, str):
        content = data.get("body") if isinstance(data.get("body"), str) else ""
    db.add(Message(conversation_id=conv.id, role="assistant", content=content))
    conv.state = "human"
    db.commit()
    return {"ok": True}
