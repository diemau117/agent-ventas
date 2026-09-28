"""Orquesta el grafo, persiste la charla y arma tarjetas interactivas."""
from sqlalchemy.orm import Session

from app.agent.graph import build_graph
from app.agent.policies import BLOCKED_MARK, GREETING_MARK
from app.config import settings
from app.crm import apply_temperature, record_interaction, signals_from_conversation, upsert_lead
from app.db.models import Business, Conversation, Message
from app.limits import check_business_limits, check_conversation_limits
from app.llm.base import LLMProvider
from app.observability import log_event
from app.services.customer import ensure_customer, profile_text


def _price_label(p: dict) -> str:
    cents = p.get("price_cents")
    if cents is None:
        return ""
    return f"${cents / 100:,.2f} {(p.get('currency') or '').strip()}".strip()


def _cards(tool_results: dict) -> list[dict]:
    """Tarjetas interactivas desde show_plans (o search_products con resultados)."""
    src = (tool_results or {}).get("show_plans") or {}
    products = src.get("products") or []
    if not products:
        sp = (tool_results or {}).get("search_products") or {}
        products = sp.get("products") or []
    out = []
    for p in products[:4]:
        out.append(
            {
                "name": p.get("name", ""),
                "description": (p.get("description") or "")[:160],
                "price_label": _price_label(p),
                "cta": f"Me interesa el plan {p.get('name', '')}".strip(),
            }
        )
    return out


async def run_chat(
    db: Session,
    llm: LLMProvider,
    business_id: int,
    conversation_id: int | None,
    message: str,
    greeting: bool = False,
    advisor: str | None = None,
) -> dict:
    biz = db.query(Business).filter_by(id=business_id).first()
    if not biz:
        raise LookupError("business_not_found")
    
    # Verificar límites del negocio antes de procesar
    check_business_limits(db, biz)
    
    if conversation_id:
        conv = db.query(Conversation).filter_by(id=conversation_id, business_id=business_id).first()
        if not conv:
            raise LookupError("conversation_not_found")
        # Verificar límites de la conversación
        check_conversation_limits(db, conversation_id)
    else:
        conv = Conversation(business_id=business_id, channel="web")
        db.add(conv)
        db.commit()
        db.refresh(conv)
    rows = (
        db.query(Message)
        .filter_by(conversation_id=conv.id)
        .order_by(Message.id.desc())
        .limit(settings.max_history)
        .all()
    )
    history = [{"role": r.role, "content": r.content} for r in reversed(rows)]
    customer = ensure_customer(db, business_id, conv)
    if advisor and customer.advisor_name != advisor:
        customer.advisor_name = advisor
        db.commit()
    graph = build_graph(llm, db)
    out = await graph.ainvoke(
        {
            "business_id": business_id,
            "conversation_id": conv.id,
            "user_message": GREETING_MARK if greeting else message,
            "history": history,
            "agent_name": customer.advisor_name,
            "profile": profile_text(customer),
        }
    )
    usage = out.get("usage", {})
    full = out.get("reply", "")
    replies = [c.strip() for c in full.split("\n\n") if c.strip()][:3] or [full]
    cards = _cards(out.get("tool_results", {}))
    decision = out.get("decision") or {}
    if not greeting:
        # H5: si el turno fue un ataque bloqueado, no persistimos el texto crudo
        # (se reinyectaría en cada turno siguiente). Guardamos el marcador.
        persisted = BLOCKED_MARK if out.get("intent") == "blocked" else message
        db.add(Message(conversation_id=conv.id, role="user", content=persisted))
    db.add(
        Message(
            conversation_id=conv.id,
            role="assistant",
            content=out.get("reply", ""),
            tokens_in=usage.get("input", 0),
            tokens_out=usage.get("output", 0),
            cost_est=usage.get("cost_est", 0.0),
        )
    )
    db.commit()

    # CRM (spec §11-12): lead por conversación + temperatura por turno.
    lead_id = None
    temperature = ""
    if not greeting and out.get("intent") != "blocked":
        lead = upsert_lead(
            db, business_id, conv.id, customer_id=conv.customer_id, source="chat"
        )
        # Listo para comprar: Jeff dijo cerrar/capturar (intención de compra).
        ready_to_buy = decision.get("next_step") in ("CLOSE", "CAPTURE_CONTACT")
        signals = signals_from_conversation(db, business_id, conv.id, ready_to_buy=ready_to_buy)
        lead = apply_temperature(db, lead, signals)
        # Próximo paso legible: la cita creada este turno manda sobre el step
        # crudo de Jeff (un humano "DISCOVER" no le dice nada).
        appt = (out.get("tool_results") or {}).get("create_appointment") or {}
        if appt.get("appointment_id"):
            from datetime import datetime as _dt

            try:
                when = _dt.fromisoformat(appt["start"]).strftime("%d/%m %H:%M")
            except (KeyError, ValueError):
                when = ""
            next_action = f"Cita agendada para {when}" if when else "Cita agendada"
        else:
            next_action = decision.get("next_step", "")
        record_interaction(
            db,
            lead,
            summary=f"{(message or '')[:200]}",
            next_action=next_action,
        )
        lead_id, temperature = lead.id, lead.temperature

    # Handoff (spec §16): pidió humano o Jeff escaló → la conversación pasa a
    # estado "human" con motivo y resumen para quien tome el control.
    if out.get("handoff"):
        from datetime import datetime

        conv.state = "human"
        conv.handoff_at = datetime.now()
        conv.handoff_reason = (out.get("handoff_reason") or "")[:200]
        conv.summary = (full or "")[:2000]
        db.commit()
        log_event(
            db,
            "handoff",
            business_id=business_id,
            conversation_id=conv.id,
            intent=out.get("intent", ""),
            decision=decision.get("next_step", ""),
            message=conv.handoff_reason,
        )
        # Spec §15: el humano trabaja en Chatwoot. Empuja el handoff con el
        # contexto completo. `push_handoff_for_business` nunca lanza: devuelve
        # {"skipped"|"ok"|"error"}, así que un fallo de Chatwoot no rompe el
        # chat (la conversación ya quedó en state="human").
        from app.integrations.chatwoot import push_handoff_for_business

        pushed = push_handoff_for_business(db, business_id, conv.id, conv.handoff_reason)
        log_event(
            db,
            "chatwoot_push",
            business_id=business_id,
            conversation_id=conv.id,
            message=str(
                "skipped" if pushed.get("skipped")
                else "ok" if pushed.get("ok")
                else pushed.get("error", "unknown")
            ),
        )

    return {
        "conversation_id": conv.id,
        "reply": full,
        "replies": replies,
        "cards": cards,
        "intent": out.get("intent", ""),
        "decision": decision,
        "handoff": bool(out.get("handoff")),
        "lead_id": lead_id,
        "lead_temperature": temperature,
        # Trazabilidad (H4): verificación y tools del turno para EventLog.
        "issues": out.get("issues") or [],
        "tools": {
            name: ("error" if isinstance(v, dict) and "error" in v else "ok")
            for name, v in (out.get("tool_results") or {}).items()
        },
        "tokens_in": usage.get("input", 0),
        "tokens_out": usage.get("output", 0),
    }
