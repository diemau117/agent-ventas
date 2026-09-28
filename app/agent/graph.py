"""Grafo LangGraph: classify → act → verify. Una llamada LLM si es smalltalk,
dos como máximo si hay tools (llamada con tools + redacción con datos)."""
import json

from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from app.agent.policies import (
    BOOKING_WORDS,
    classify_intent,
    is_booking_confirmation,
    is_injection,
    needs_tools,
    pick_slot,
)
from app.agent.jeff import decide as jeff_decide
from app.agent.prompts import (
    FALLBACK_NO_INFO,
    REFUSAL_INTERNAL,
    STEP_DIRECTIVES,
    SYSTEM_TEMPLATE,
)
from app.agent.state import AgentState
from app.agent.verifier import verify_response
from app.config import settings
from app.db.models import Business
from app.llm.base import LLMProvider
from app.tools.registry import TOOL_DEFS, execute_tool


_UPDATE_CUSTOMER_DEF = next(d for d in TOOL_DEFS if d["function"]["name"] == "update_customer")


def _tool_ok(result) -> bool:
    """H8: un tool que devolvió {"error": ...} no es información autorizada."""
    return isinstance(result, dict) and "error" not in result


def _directive(step: str) -> str:
    """Bloque de dirección comercial de Jeff para el system prompt (§13).

    Jeff decidía pero el modelo nunca se enteraba (AUDITORIA_VENTAS):
    con esto, cada llamada al LLM sabe si le toca cerrar, agendar,
    manejar objeción o descubrir.
    """
    text = STEP_DIRECTIVES.get(step)
    return f"\n\n## Dirección de este turno (Jeff)\n{text}" if text else ""


def _authorize(messages: list, label: str, result: dict, limit: int = 2000) -> bool:
    """Inyecta el resultado al prompt SOLO si no trae error. Devuelve si se inyectó."""
    if not _tool_ok(result):
        return False
    messages.append(
        {
            "role": "user",
            "content": f"Información autorizada ({label}): " + json.dumps(result, ensure_ascii=False)[:limit],
        }
    )
    return True


def _system(db: Session, business_id: int, agent_name: str = "",
            intent: str = "", message_count: int = 0) -> str:
    b = db.query(Business).filter_by(id=business_id).first()
    name = b.name if b else "el negocio"
    agent = agent_name or (b.agent_name if b and b.agent_name else "Sofi")
    base = (b.persona if b and b.persona else "consultiva")
    from app.agent.personas import dynamic_persona, persona_block

    override = dynamic_persona(base, intent, message_count)
    return SYSTEM_TEMPLATE.format(
        business_name=name, agent_name=agent,
        persona=persona_block(base, override),
    )


def _jeff_context(db: Session, state: AgentState, tool_results: dict) -> dict:
    """Arma el context de Jeff (spec §13) desde la BD y el estado del turno."""
    biz = db.query(Business).filter_by(id=state["business_id"]).first()
    hits = len(((tool_results or {}).get("search_knowledge") or {}).get("entries") or [])
    history = state.get("history") or []
    from app.db.models import Appointment as Appt

    conv_id = state.get("conversation_id")
    has_appt = bool(
        conv_id
        and db.query(Appt.id)
        .filter(
            Appt.business_id == state["business_id"],
            Appt.conversation_id == conv_id,
            Appt.status == "confirmed",
        )
        .first()
    )
    return {
        "intent": state.get("intent", ""),
        "user_message": state.get("user_message", ""),
        "history": history,
        "tool_results": tool_results or {},
        "profile": state.get("profile", ""),
        "knowledge_hits": hits,
        "business_hours": biz.hours if biz else "",
        "business_address": biz.address if biz else "",
        "business_phone": biz.phone if biz else "",
        "message_count": len(history) + 1,
        "has_appointment": has_appt,
        "external_search_enabled": bool(
            biz and biz.external_search_enabled
        ) and settings.external_search_enabled,
    }


def _maybe_external(db: Session, state: AgentState, messages: list, decision) -> dict:
    """Búsqueda externa segura (spec §14) si Jeff la pidió.

    Devuelve el resultado de `search_external` para trazabilidad, o {}.
    """
    if not decision.needs_external:
        return {}
    from app.tools.websearch import format_results, search_external

    res = search_external(
        db, state["business_id"], state["conversation_id"], state["user_message"], k=3
    )
    if not res.get("allowed") or not res.get("results"):
        return res
    messages.append(
        {
            "role": "user",
            "content": "Información externa (NO oficial de la empresa, solo contexto):\n"
            + format_results(res["results"])[:2000],
        }
    )
    return res


def build_graph(llm: LLMProvider, db: Session):
    g = StateGraph(AgentState)

    async def classify(state: AgentState) -> dict:
        msg = state["user_message"]
        if is_injection(msg):
            return {"intent": "blocked", "blocked": True}
        intent = classify_intent(msg)
        # Confirmación de cita con slots ofrecidos arriba → conversión.
        # "sí, dale" sin slots en la historia sigue siendo general.
        if intent in ("general", "smalltalk", "greeting") and is_booking_confirmation(
            msg, state.get("history")
        ):
            intent = "conversion"
        return {"intent": intent, "blocked": False}

    async def act(state: AgentState) -> dict:
        usage = {"input": 0, "output": 0, "cost_est": 0.0}
        if state.get("blocked"):
            return {
                "draft": REFUSAL_INTERNAL, "tool_results": {}, "usage": usage,
                "decision": {"next_step": "ANSWER", "reason": "bloqueo de inyección"},
                "handoff": False, "handoff_reason": "",
            }
        messages = [{"role": "system", "content": _system(
            db, state["business_id"], state.get("agent_name", ""),
            state.get("intent", ""), len(state.get("history") or []) + 1,
        )}]
        # Dirección de Jeff ANTES de la 1ª llamada (decisión preliminar sin
        # tools): el modelo sabe desde el inicio si le toca cerrar, agendar,
        # manejar objeción o descubrir. Con tools la redecide y la 2ª llamada
        # recibe la versión final.
        pre_decision = jeff_decide(_jeff_context(db, state, {}))
        sys_base = messages[0]["content"]
        messages[0]["content"] = sys_base + _directive(pre_decision.next_step)
        if state.get("profile") and not state.get("history"):
            # H12: el perfil es contexto a confirmar, no verdad autorizada.
            messages.append({"role": "user", "content": f"Perfil del cliente (a confirmar): {state['profile']}"})
        messages += state.get("history", [])
        messages.append({"role": "user", "content": state["user_message"]})
        use_tools = needs_tools(state.get("intent", "general"))
        tool_results: dict = {}
        # Jeff (spec §13): defaults hasta que corra en la rama con tools.
        payload: dict = {"next_step": "DISCOVER", "reason": "default"}
        handoff = False
        handoff_reason = ""
        # Hard limits: contadores para prevenir loops infinitos
        tool_call_count = 0
        max_tool_calls = settings.max_tool_calls_per_turn
        max_steps = settings.max_agent_steps
        step_count = 0
        if use_tools:
            first = await llm.chat(messages, tools=TOOL_DEFS)
            for k in usage:
                usage[k] += first.usage.get(k, 0)
            for call in first.tool_calls:
                # Hard limit: MAX_TOOL_CALLS_PER_TURN
                if tool_call_count >= max_tool_calls:
                    break
                tool_call_count += 1
                step_count += 1
                if step_count > max_steps:
                    break
                tool_results[call.name] = execute_tool(
                    db, state["business_id"], state["conversation_id"], call.name, call.args
                )
                # H8: si el tool falló, no se rotula "Información autorizada".
                _authorize(messages, call.name, tool_results[call.name])
            # Retrieval forzado por el backend: el LLM no controla el acceso a datos.
            if state.get("intent") in ("pricing", "discovery") and "search_products" not in tool_results:
                tool_results["search_products"] = execute_tool(
                    db, state["business_id"], state["conversation_id"],
                    "search_products", {"query": state["user_message"]},
                )
                _authorize(messages, "search_products", tool_results["search_products"])
            if state.get("intent") in ("pricing", "discovery") and "show_plans" not in tool_results:
                tool_results["show_plans"] = execute_tool(
                    db, state["business_id"], state["conversation_id"],
                    "show_plans", {},
                )
                _authorize(messages, "show_plans", tool_results["show_plans"])
            low_msg = state["user_message"].lower()
            if state.get("intent") == "conversion" and any(w in low_msg for w in BOOKING_WORDS) and "check_availability" not in tool_results:
                tool_results["check_availability"] = execute_tool(
                    db, state["business_id"], state["conversation_id"],
                    "check_availability", {},
                )
                _authorize(messages, "check_availability", tool_results["check_availability"])
            # Cita forzada (spec §10): el cliente confirmó con slots ofrecidos
            # → el backend agenda. El LLM solo redacta; no depende de que el
            # modelo elija el slot exacto (era el modo en que se perdían ventas).
            confirmed = is_booking_confirmation(
                state.get("user_message", ""), state.get("history")
            )
            appt_res = tool_results.get("create_appointment")
            if confirmed and not (isinstance(appt_res, dict) and appt_res.get("appointment_id")):
                if "check_availability" not in tool_results:
                    tool_results["check_availability"] = execute_tool(
                        db, state["business_id"], state["conversation_id"],
                        "check_availability", {},
                    )
                _authorize(messages, "check_availability", tool_results["check_availability"])
                start = pick_slot(
                    state.get("user_message", ""),
                    (tool_results.get("check_availability") or {}).get("slots") or [],
                )
                if start:
                    tool_results["create_appointment"] = execute_tool(
                        db, state["business_id"], state["conversation_id"],
                        "create_appointment", {"start": start},
                    )
                    _authorize(messages, "create_appointment", tool_results["create_appointment"])
            # RAG forzado (spec §3-4): la knowledge de la empresa entra solo si
            # hay match. Sin matches no se inyecta nada (un "entries: []" no es
            # información autorizada y haría pasar el gate H8).
            if "search_knowledge" not in tool_results:
                sk = execute_tool(
                    db, state["business_id"], state["conversation_id"],
                    "search_knowledge", {"query": state["user_message"]},
                )
                if sk.get("entries"):
                    tool_results["search_knowledge"] = sk
                    _authorize(messages, "search_knowledge", sk)
            # H2: horario/direccion como evidencia disponible para el verificador,
            # aunque la query del cliente no traiga esos docs por scoring.
            from app.rag import categories_for

            fact_docs = categories_for(db, state["business_id"], ("horario", "direccion"))
            if fact_docs:
                existing = tool_results.get("search_knowledge") or {"entries": []}
                seen = {e.get("id") for e in existing.get("entries", [])}
                merged = list(existing.get("entries", [])) + [
                    e for e in fact_docs if e.get("id") not in seen
                ]
                tool_results["search_knowledge"] = {"entries": merged}
                _authorize(messages, "search_knowledge", tool_results["search_knowledge"])
            # Jeff (spec §13): decide el turno con la evidencia ya cargada.
            ctx = _jeff_context(db, state, tool_results)
            decision = jeff_decide(ctx)
            payload = {
                "next_step": decision.next_step,
                "reason": decision.reason,
                "needs_rag": decision.needs_rag,
                "needs_external": decision.needs_external,
                "escalate": decision.escalate,
                "escalate_reason": decision.escalate_reason,
                "lead_temperature_hint": decision.lead_temperature_hint,
            }
            handoff = decision.escalate
            handoff_reason = decision.escalate_reason
            # Tres casos, no dos (P0 de AUDITORIA_VENTAS): vacío ≠ fallaron.
            has_tools = bool(tool_results)
            any_ok = any(_tool_ok(v) for v in tool_results.values())
            if has_tools and not any_ok:
                # H8 real: los tools existieron y TODOS devolvieron error →
                # fallback y sin 2ª llamada (evita que el modelo "redacte" errores).
                return {
                    "draft": FALLBACK_NO_INFO, "tool_results": tool_results,
                    "usage": usage, "decision": payload,
                    "handoff": handoff, "handoff_reason": handoff_reason,
                }
            # Búsqueda externa (§14) solo si Jeff la autorizó.
            ext = _maybe_external(db, state, messages, decision)
            if ext.get("allowed") and ext.get("results") and ext.get("external_search_id"):
                from app.tools.websearch import mark_used

                mark_used(db, ext["external_search_id"])
            has_ext = bool(ext.get("allowed") and ext.get("results"))
            if not has_tools and not has_ext:
                # El LLM respondió en crudo, sin tool calls: es exactamente lo
                # que hace al objetar precio, comparar o descubrir. Su texto es
                # el draft — pasa por verify igual que cualquier otro.
                return {
                    "draft": first.text or FALLBACK_NO_INFO,
                    "tool_results": tool_results,
                    "usage": usage, "decision": payload,
                    "handoff": handoff, "handoff_reason": handoff_reason,
                }
            if tool_results:
                messages.append(
                    {
                        "role": "user",
                        "content": "Información autorizada arriba. Responde breve usando SOLO esos datos.",
                    }
                )
            # Directiva final de Jeff (decidida con los tools ya cargados):
            # reemplaza la preliminar de la 1ª llamada.
            messages[0]["content"] = sys_base + _directive(decision.next_step)
            second = await llm.chat(messages)
            for k in usage:
                usage[k] += second.usage.get(k, 0)
            draft = second.text or FALLBACK_NO_INFO
        else:
            # Smalltalk con memoria: el modelo solo puede ofrecer update_customer.
            # Jeff igual decide aquí (§13): sin tools no hay retrieval, pero sí
            # detecta handoff/escalado por petición de humano o queja.
            ctx_small = _jeff_context(db, state, tool_results)
            decision_small = jeff_decide(ctx_small)
            payload = {
                "next_step": decision_small.next_step,
                "reason": decision_small.reason,
                "needs_rag": decision_small.needs_rag,
                "needs_external": decision_small.needs_external,
                "escalate": decision_small.escalate,
                "escalate_reason": decision_small.escalate_reason,
                "lead_temperature_hint": decision_small.lead_temperature_hint,
            }
            handoff = decision_small.escalate
            handoff_reason = decision_small.escalate_reason
            r = await llm.chat(messages, tools=[_UPDATE_CUSTOMER_DEF])
            for k in usage:
                usage[k] += r.usage.get(k, 0)
            saved = [c for c in r.tool_calls if c.name == "update_customer"]
            if not saved:
                draft = r.text or FALLBACK_NO_INFO
            else:
                for call in saved:
                    res = execute_tool(
                        db, state["business_id"], state["conversation_id"], call.name, call.args
                    )
                    # H8: solo se rotula si update_customer no devolvió error.
                    if not _authorize(messages, "update_customer", res, limit=500):
                        draft = FALLBACK_NO_INFO
                        return {
                            "draft": draft, "tool_results": tool_results, "usage": usage,
                            "decision": payload, "handoff": handoff,
                            "handoff_reason": handoff_reason,
                        }
                r2 = await llm.chat(messages)
                for k in usage:
                    usage[k] += r2.usage.get(k, 0)
                draft = r2.text or FALLBACK_NO_INFO
        return {
            "draft": draft, "tool_results": tool_results, "usage": usage,
            "decision": payload, "handoff": handoff, "handoff_reason": handoff_reason,
        }

    async def verify(state: AgentState) -> dict:
        if state.get("blocked"):
            return {"reply": state["draft"], "issues": []}
        v = verify_response(state.get("draft", ""), state.get("tool_results", {}))
        if v["approved"]:
            return {"reply": state["draft"], "issues": v["issues"]}
        return {"reply": FALLBACK_NO_INFO, "issues": v["issues"]}

    g.add_node("classify", classify)
    g.add_node("act", act)
    g.add_node("verify", verify)
    g.add_edge("classify", "act")
    g.add_edge("act", "verify")
    g.add_edge("verify", END)
    g.set_entry_point("classify")
    return g.compile()
