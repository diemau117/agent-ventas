"""Integración Chatwoot + handoff al humano (spec §15-16).

- `build_payload` / `format_handoff_text`: contexto completo del lead para que
  el humano que recibe el handoff no tenga que preguntar "¿qué necesitas?".
- `ChatwootClient`: cliente httpx sincrónico. Nunca lanza excepciones al
  caller: devuelve skip (`{"skipped": True, ...}`), ok (`{"ok": True, ...}`)
  o error (`{"ok": False, "error": ...}`). Acepta `transport`/`client` para
  testear con `httpx.MockTransport` sin red.
- `push_handoff_for_business`: entrada que invoca el orquestador al handoff.
"""

import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Appointment, Business, Conversation, Customer, Lead, Message

# Últimas interacciones que viajan en el handoff.
_TAIL = 10

# Marcador que llevan nuestros mensajes pushados para que el webhook de
# Chatwoot no los re-persista (evita bucle spec §15).
SOURCE_MARK = "agent_ventas"


@dataclass
class HandoffPayload:
    """Contexto completo que recibe el humano al tomar la conversación."""

    conversation_id: int
    business_id: int
    customer_name: str = ""
    company: str = ""
    need: str = ""
    problem: str = ""
    service_interest: str = ""
    budget: str = ""
    urgency: str = ""
    summary: str = ""
    reason: str = ""
    temperature: str = ""
    # Próxima cita confirmada de la conversación (spec §10/§16).
    appointment: str = ""
    messages_tail: list[dict] = field(default_factory=list)


def _auto_summary(lead: Lead | None) -> str:
    """Resumen en una frase (español): necesidad + problema + servicio + próximo paso."""
    if lead is None:
        return ""
    partes = []
    if lead.need:
        partes.append(lead.need.rstrip(". "))
    if lead.problem:
        partes.append(f"problema: {lead.problem.rstrip('. ')}")
    if lead.service_interest:
        partes.append(f"servicio de interés: {lead.service_interest.rstrip('. ')}")
    if lead.next_action:
        partes.append(f"próximo paso: {lead.next_action.rstrip('. ')}")
    if not partes:
        return ""
    frase = "; ".join(partes)
    return frase[0].upper() + frase[1:] + "."


def build_payload(db: Session, business_id: int, conversation_id: int, reason: str) -> HandoffPayload:
    """Arma el HandoffPayload desde la BD, aislado por business_id.

    Lee Conversation (summary), Customer (name, company), el Lead más reciente
    de la conversación (need, problem, service_interest, budget, urgency,
    temperature, next_action) y los últimos ~10 Messages.

    Nombre: Customer.name, o el del Lead, o vacío. Si Conversation.summary está
    vacío se arma con necesidad + problema + servicio + próximo paso.
    Lanza LookupError("conversation_not_found") si la conversación no existe
    en ese tenant.
    """
    conv = db.query(Conversation).filter_by(id=conversation_id, business_id=business_id).first()
    if conv is None:
        raise LookupError("conversation_not_found")

    customer = None
    if conv.customer_id:
        customer = (
            db.query(Customer).filter_by(id=conv.customer_id, business_id=business_id).first()
        )
    lead = (
        db.query(Lead)
        .filter(Lead.business_id == business_id, Lead.conversation_id == conversation_id)
        .order_by(Lead.id.desc())
        .first()
    )
    # La conversación ya quedó validada por tenant: sus mensajes son del tenant.
    rows = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(_TAIL)
        .all()
    )

    customer_name = ((customer.name if customer else "") or (lead.name if lead else "") or "").strip()
    company = ((customer.company if customer else "") or (lead.company if lead else "") or "").strip()
    summary = (conv.summary or "").strip() or _auto_summary(lead)

    # Próxima cita confirmada de esta conversación (la necesita el humano).
    appt = (
        db.query(Appointment)
        .filter(
            Appointment.business_id == business_id,
            Appointment.conversation_id == conversation_id,
            Appointment.status == "confirmed",
            Appointment.start >= datetime.now(),
        )
        .order_by(Appointment.start.asc())
        .first()
    )

    return HandoffPayload(
        conversation_id=conversation_id,
        business_id=business_id,
        customer_name=customer_name,
        company=company,
        need=(lead.need if lead else ""),
        problem=(lead.problem if lead else ""),
        service_interest=(lead.service_interest if lead else ""),
        budget=(lead.budget if lead else ""),
        urgency=(lead.urgency if lead else ""),
        summary=summary,
        reason=reason,
        temperature=(lead.temperature if lead else ""),
        appointment=(appt.start.strftime("%d/%m/%Y %H:%M") if appt else ""),
        messages_tail=[{"role": r.role, "content": r.content} for r in reversed(rows)],
    )


_ROLE_LABELS = {"user": "Cliente", "assistant": "Agente", "system": "Sistema"}


def format_handoff_text(payload: HandoffPayload) -> str:
    """Texto legible con etiquetas en español: es el mensaje que se pusha a Chatwoot."""
    lineas = [
        f"Handoff de conversación #{payload.conversation_id}",
        "",
        f"Nombre: {payload.customer_name}",
        f"Empresa: {payload.company}",
        f"Necesidad: {payload.need}",
        f"Problema: {payload.problem}",
        f"Servicio de interés: {payload.service_interest}",
        f"Presupuesto: {payload.budget}",
        f"Urgencia: {payload.urgency}",
        f"Temperatura: {payload.temperature}",
        f"Próxima cita: {payload.appointment or 'sin cita'}",
        f"Motivo: {payload.reason}",
        f"Resumen: {payload.summary}",
    ]
    if payload.messages_tail:
        lineas.append("")
        lineas.append("Interacciones recientes:")
        for m in payload.messages_tail:
            rol = m.get("role") or ""
            etiqueta = _ROLE_LABELS.get(rol, rol or "Sistema")
            lineas.append(f"{etiqueta}: {m.get('content') or ''}")
    return "\n".join(lineas)


class ChatwootClient:
    """Cliente HTTP de la API de Chatwoot (api_access_token en header)."""

    def __init__(
        self,
        base_url: str,
        api_token: str,
        inbox_id: int = 0,
        account_id: str = "1",
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (base_url or "").strip().rstrip("/")
        self.api_token = (api_token or "").strip()
        self.inbox_id = int(inbox_id or 0)
        self.account_id = str(account_id or "1")
        self._transport = transport
        self._client = client

    @property
    def configured(self) -> bool:
        """True si hay URL y token de API. Sin eso, cero red y cero excepciones."""
        return bool(self.base_url and self.api_token)

    @contextmanager
    def _session(self):
        """Client inyectado (tests) o uno efímero con transport propio."""
        if self._client is not None:
            yield self._client
            return
        with httpx.Client(timeout=15.0, transport=self._transport) as http:
            yield http

    def _post(self, path: str, body: dict) -> dict:
        """POST JSON a la API de Chatwoot. Nunca lanza: devuelve ok | error | skipped."""
        if not self.configured:
            return {"skipped": True, "reason": "chatwoot_not_configured"}
        headers = {"api_access_token": self.api_token, "Content-Type": "application/json"}
        try:
            with self._session() as http:
                r = http.post(self.base_url + path, headers=headers, json=body)
            if r.status_code >= 400:
                return {"ok": False, "error": f"http_{r.status_code}"}
            try:
                data = r.json()
            except ValueError:
                data = {}
            return {"ok": True, "data": data if isinstance(data, dict) else {}}
        except Exception as e:  # red/timeout/cualquier fallo: nunca excepción al caller
            return {"ok": False, "error": str(e) or type(e).__name__}

    def send_message(self, conversation_id: int, content: str) -> dict:
        """Publica `content` en la conversación de Chatwoot como nota interna
        marcada (SOURCE_MARK): el webhook propio la ignora y no hay bucle."""
        return self._post(
            f"/api/v1/accounts/{self.account_id}/conversations/{conversation_id}/messages",
            {
                "content": content,
                "message_type": "outgoing",
                "private": True,
                "content_attributes": {"source": SOURCE_MARK},
            },
        )

    def push_handoff(self, payload: HandoffPayload) -> dict:
        """Crea la conversación en Chatwoot y envía `format_handoff_text(payload)`.

        Flujo API de Chatwoot: contacto (+contact_inbox con source_id propio,
        obligatorio para crear la conversación) → conversación abierta con
        atributos de correlación interna → brief como nota interna.
        Devuelve skip | {"ok": True, "conversation_id": ...} | {"ok": False, ...}.
        """
        if not self.configured:
            return {"skipped": True, "reason": "chatwoot_not_configured"}
        if self.inbox_id <= 0:
            return {"ok": False, "error": "chatwoot_inbox_missing"}

        source_id = f"av-{payload.business_id}-{payload.conversation_id}-{uuid.uuid4().hex[:12]}"
        res = self._post(
            f"/api/v1/accounts/{self.account_id}/contacts",
            {
                "inbox_id": self.inbox_id,
                "name": payload.customer_name or "Cliente",
                "source_id": source_id,
            },
        )
        if not res.get("ok"):
            return res
        res = self._post(
            f"/api/v1/accounts/{self.account_id}/conversations",
            {
                "source_id": source_id,
                "inbox_id": self.inbox_id,
                "status": "open",
                # El webhook usa estos atributos para volver a la conversación
                # interna aunque el id de Chatwoot no coincida con el nuestro.
                "additional_attributes": {
                    "agent_ventas_conversation_id": payload.conversation_id,
                    "agent_ventas_business_id": payload.business_id,
                },
            },
        )
        if not res.get("ok"):
            return res
        try:
            conv_ref = int(res["data"].get("id"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "chatwoot_conversation_without_id"}

        enviado = self.send_message(conv_ref, format_handoff_text(payload))
        if not enviado.get("ok"):
            return enviado
        return {"ok": True, "conversation_id": conv_ref}


def push_handoff_for_business(
    db: Session, business_id: int, conversation_id: int, reason: str
) -> dict:
    """Handoff al humano vía Chatwoot para un negocio (entrada del orquestador).

    Credenciales por negocio (Business.chatwoot_url/token/inbox_id); si están
    vacías caen al default de instancia (settings.chatwoot_url/token — "Chatwoot
    por defecto; cada negocio puede sobreescribir en BD"). El inbox es solo por
    negocio. Devuelve skip | ok | error, nunca lanza.
    """
    biz = db.query(Business).filter_by(id=business_id).first()
    if biz is None:
        return {"ok": False, "error": "business_not_found"}
    client = ChatwootClient(
        base_url=(biz.chatwoot_url or "").strip() or settings.chatwoot_url,
        api_token=(biz.chatwoot_token or "").strip() or settings.chatwoot_token,
        inbox_id=biz.chatwoot_inbox_id or 0,
    )
    if not client.configured:
        return {"skipped": True, "reason": "chatwoot_not_configured"}
    try:
        payload = build_payload(db, business_id, conversation_id, reason)
    except LookupError as e:
        return {"ok": False, "error": str(e)}
    return client.push_handoff(payload)
