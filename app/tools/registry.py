"""Tools Fase 1-2. Todo filtrado por business_id: el LLM nunca elige tenant."""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.db.models import Appointment, Business, Customer, Lead, Product

# H12: allowlist de claves que el LLM puede guardar en Customer.facts.
# Sin esto, el modelo podía persistir claves/valores arbitrarios y el perfil
# se reinyectaba cada turno como contexto "autorizado".
ALLOWED_FACT_KEYS = frozenset(
    {
        "nombre_negocio",
        "negocio",
        "rubro",
        "industria",
        "dolor",
        "necesidad",
        "busca",
        "preferencia_contacto",
        "presupuesto",
        "urgencia",
        "web",
        "empleados",
        "nota",
        "notas",
    }
)

TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "get_business_info",
            "description": "Datos básicos del negocio.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": "Buscar productos activos del negocio por texto.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_plans",
            "description": "Mostrar el catálogo como tarjetas interactivas. Usala cuando pregunten por planes, precios u opciones del servicio.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_lead",
            "description": "Registrar un lead con nombre/contacto del cliente.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "contact": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["contact"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_customer",
            "description": "Perfil del cliente actual (nombre, teléfono, datos previos). Llamala al inicio si no tenés contexto.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_customer",
            "description": "Guardar datos nuevos del cliente (nombre, teléfono, negocio, rubro, dolor, preferencias).",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "phone": {"type": "string"},
                    "facts": {
                        "type": "object",
                        # H12: el schema ya no ofrece claves arbitrarias.
                        "propertyNames": {"enum": sorted(ALLOWED_FACT_KEYS)},
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "Buscar en la base de conocimiento de la empresa (horarios, políticas, FAQ, procesos, condiciones). Usala para preguntas sobre la empresa que no son de precio ni de catálogo.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_availability",
            "description": "Horarios libres para agendar llamada con Diego (slots de 1 hora a las 10:00 o 15:00, lun-vie). days = horizonte en días (7 = la semana).",
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer"}},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_appointment",
            "description": "Agendar la llamada. Solo usar con un start devuelto por check_availability y confirmación del cliente.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {"type": "string", "description": "ISO: 2026-09-14T10:00:00"},
                    "name": {"type": "string"},
                    "contact": {"type": "string"},
                },
                "required": ["start"],
                "additionalProperties": False,
            },
        },
    },
]


SLOT_START, SLOT_END = 9, 17  # lun-vie, slots de 1 hora
# Horas fijas por día hábil: los slots se reparten por el horizonte entero
# para que "agendar el martes" encuentre martes (spec §10 — day-aware).
_SLOT_HOURS = (10, 15)


def _slot_grid(day) -> list[datetime]:
    """Slots candidatos de un día datetime (10:00 y 15:00, si caen en ventana)."""
    return [
        day.replace(hour=h, minute=0, second=0, microsecond=0)
        for h in _SLOT_HOURS
        if SLOT_START <= h < SLOT_END
    ]


def _free_slots(db: Session, business_id: int, days: int = 7, limit: int = 6) -> list[str]:
    """Próximos `limit` slots libres repartidos por `days` días calendario.

    Antes devolvía los próximos N slots HOURLY desde ahora+1h: con limit=6
    solo alcanzaba hoy/manñana y "el martes" era inalcanzable.
    Si el horizonte pedido no alcanza ningún día hábil (p. ej. days=1 caído
    en sábado), se extiende a 7 días en vez de devolver vacío.
    """
    slots = _free_slots_horizon(db, business_id, days, limit)
    if not slots and days < 7:
        slots = _free_slots_horizon(db, business_id, 7, limit)
    return slots


def _free_slots_horizon(db: Session, business_id: int, days: int, limit: int) -> list[str]:
    taken = {
        a.start.replace(minute=0, second=0, microsecond=0)
        for a in db.query(Appointment)
        .filter(Appointment.business_id == business_id, Appointment.status == "confirmed")
        .all()
    }
    horizon = datetime.now() + timedelta(days=max(1, min(days, 60)))
    out: list[str] = []
    day = datetime.now().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    day = day.replace(hour=0)  # barrer por días completos desde hoy
    while day <= horizon and len(out) < limit:
        if day.weekday() < 5:
            for slot in _slot_grid(day):
                if slot > datetime.now() and slot not in taken and len(out) < limit:
                    out.append(slot.isoformat(timespec="seconds"))
        day += timedelta(days=1)
    return out


def _appt_context(lead: "Lead | None") -> str:
    """Contexto compacto que recibe el vendedor con la cita (spec §10)."""
    if not lead:
        return ""
    parts = [
        p
        for p in (
            f"Lead #{lead.id} ({lead.status})",
            f"Necesidad: {lead.need}" if lead.need else "",
            f"Problema: {lead.problem}" if lead.problem else "",
            f"Servicio: {lead.service_interest}" if lead.service_interest else "",
            f"Resumen: {lead.ai_summary}" if lead.ai_summary else "",
        )
        if p
    ]
    return " | ".join(parts)[:2000]


def _money(p: Product) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "price_cents": p.price_cents,
        "currency": p.currency,
    }


def execute_tool(
    db: Session, business_id: int, conversation_id: int, name: str, args: dict
) -> dict:
    try:
        if name == "get_business_info":
            b = db.query(Business).filter_by(id=business_id).first()
            if not b:
                return {"error": "not_found"}
            return {"name": b.name, "description": b.description, "tone": b.tone}
        if name == "search_products":
            q = (args.get("query") or "")[:100]
            # H6: escapar LIKE exige pasar escape= al ilike; sin eso el escape
            # anterior no tenía efecto y "%" / "_" eran comodines.
            esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            rows = (
                db.query(Product)
                .filter(Product.business_id == business_id, Product.active.is_(True))
                .filter(
                    Product.name.ilike(f"%{esc}%", escape="\\")
                    | Product.description.ilike(f"%{esc}%", escape="\\")
                )
                .limit(5)
                .all()
            )
            # H13: el fallback a catálogo completo solo aplica a queries
            # genéricas cortas. Una query larga sin match devuelve vacío en vez
            # de grounding con productos no pedidos.
            if not rows and len(q.strip()) <= 20:
                rows = (
                    db.query(Product)
                    .filter(Product.business_id == business_id, Product.active.is_(True))
                    .limit(5)
                    .all()
                )
            return {"products": [_money(p) for p in rows]}
        if name == "search_knowledge":
            # RAG (spec §3-4): retrieval léxico sobre la knowledge del tenant.
            from app.rag import retrieve

            entries = retrieve(db, business_id, (args.get("query") or "")[:200], k=4)
            return {"entries": entries}
        if name == "show_plans":
            rows = (
                db.query(Product)
                .filter(Product.business_id == business_id, Product.active.is_(True))
                .limit(5)
                .all()
            )
            return {"products": [_money(p) for p in rows]}
        if name == "create_lead":
            contact = (args.get("contact") or "").strip()
            if not contact:
                return {"error": "contact_required"}
            # Reutiliza el lead de la conversación si ya existe (idempotente).
            lead = (
                db.query(Lead)
                .filter(Lead.business_id == business_id, Lead.conversation_id == conversation_id)
                .first()
            )
            if lead is None:
                lead = Lead(business_id=business_id, conversation_id=conversation_id, source="chat")
                db.add(lead)
            # contact puede ser email o teléfono: el que calce, va a su columna.
            if "@" in contact:
                lead.email = contact[:200]
            else:
                lead.phone = contact[:100]
            if args.get("name"):
                lead.name = str(args["name"])[:200]
            if args.get("note") and not lead.problem:
                lead.problem = str(args["note"])[:500]
            if lead.status == "new":
                lead.status = "working"
            db.commit()
            return {"lead_id": lead.id, "status": lead.status}
        if name in ("get_customer", "update_customer"):
            from app.db.models import Conversation as Conv

            conv = db.query(Conv).filter_by(id=conversation_id, business_id=business_id).first()
            if not conv or not conv.customer_id:
                return {"error": "no_customer"}
            c = db.query(Customer).filter_by(id=conv.customer_id, business_id=business_id).first()
            if not c:
                return {"error": "no_customer"}
            if name == "update_customer":
                if args.get("name"):
                    c.name = args["name"][:200]
                if args.get("phone"):
                    c.phone = args["phone"][:100]
                if isinstance(args.get("facts"), dict):
                    # H12: solo claves de la allowlist; el resto se descarta.
                    allowed = {
                        k: str(v)[:300]
                        for k, v in args["facts"].items()
                        if k in ALLOWED_FACT_KEYS
                    }
                    c.facts = {**(c.facts or {}), **allowed}
                db.commit()
            return {
                "name": c.name, "phone": c.phone,
                "advisor": c.advisor_name, "facts": c.facts or {},
            }
        if name == "check_availability":
            return {"slots": _free_slots(db, business_id, int(args.get("days") or 7))}
        if name == "create_appointment":
            try:
                start = datetime.fromisoformat(args.get("start") or "")
            except ValueError:
                return {"error": "invalid_slot"}
            if start <= datetime.now() or start.weekday() >= 5 or not (SLOT_START <= start.hour < SLOT_END) or start.minute:
                return {"error": "invalid_slot"}
            clash = (
                db.query(Appointment)
                .filter(Appointment.business_id == business_id,
                        Appointment.status == "confirmed",
                        Appointment.start == start)
                .first()
            )
            if clash:
                return {"error": "slot_taken"}
            # CRM (spec §10-12): la cita se liga al lead de la conversación,
            # arrastra su contexto y lo hace "qualified" (cita = avance real).
            lead = None
            if conversation_id:
                lead = (
                    db.query(Lead)
                    .filter(Lead.business_id == business_id,
                            Lead.conversation_id == conversation_id)
                    .order_by(Lead.id.desc())
                    .first()
                )
            a = Appointment(
                business_id=business_id, conversation_id=conversation_id,
                lead_id=lead.id if lead else None,
                customer_name=(args.get("name") or (lead.name if lead else ""))[:200],
                contact=(args.get("contact") or (lead.phone or lead.email if lead else ""))[:200],
                company=(lead.company if lead else "")[:200],
                need=(lead.need if lead else "")[:2000],
                context=_appt_context(lead),
                start=start,
            )
            db.add(a)
            if lead and lead.status in ("new", "working"):
                lead.status = "qualified"
                lead.next_action = f"Cita agendada para {start.strftime('%d/%m %H:%M')}"
            db.commit()
            return {"appointment_id": a.id, "start": start.isoformat(timespec="seconds"), "status": "confirmed"}
        return {"error": "unknown_tool"}
    except Exception:
        db.rollback()
        return {"error": "tool_failed"}
