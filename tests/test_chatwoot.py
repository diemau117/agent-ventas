"""Chatwoot + handoff (spec §15-16): cliente, payload y webhook."""

import json
from contextlib import contextmanager
from pathlib import Path
from tempfile import gettempdir

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes import webhook
from app.config import settings
from app.db.database import get_db
from app.db.models import Base, Business, Conversation, Customer, Lead, Message
from app.integrations.chatwoot import (
    ChatwootClient,
    HandoffPayload,
    build_payload,
    format_handoff_text,
    push_handoff_for_business,
)

NEED = "una landing con formulario"
PROBLEM = "pierdo clientes del sitio actual"
SERVICE = "Landing + Chatbot"
BUDGET = "USD 500"
URGENCY = "este mes"
NEXT = "Enviar propuesta el viernes"


engine = create_engine(
    f"sqlite:///{Path(gettempdir()) / 'agent_chatwoot_test.db'}",
    connect_args={"check_same_thread": False},
)
TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _seed() -> dict:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = TestSession()
    biz = Business(name="Café Demo", description="d")  # sin chatwoot_* → tenant sin integración
    db.add(biz)
    db.commit()
    cust = Customer(business_id=biz.id, name="María López", company="ACME")
    db.add(cust)
    db.commit()
    conv = Conversation(business_id=biz.id, customer_id=cust.id, channel="web")
    db.add(conv)
    db.commit()
    db.add(
        Lead(
            business_id=biz.id,
            conversation_id=conv.id,
            customer_id=cust.id,
            name="María López",
            company="ACME",
            need=NEED,
            problem=PROBLEM,
            service_interest=SERVICE,
            budget=BUDGET,
            urgency=URGENCY,
            temperature="caliente",
            next_action=NEXT,
        )
    )
    db.add(Message(conversation_id=conv.id, role="user", content="Hola, necesito una web"))
    db.add(
        Message(
            conversation_id=conv.id,
            role="assistant",
            content="¡Hola! Contame qué necesitás.",
        )
    )
    db.commit()
    ids = {"biz": biz.id, "conv": conv.id}
    for key in ("humano", "bot", "real", "tip", "verif"):
        c = Conversation(business_id=biz.id, customer_id=cust.id)
        db.add(c)
        db.commit()
        ids[key] = c.id
    # Conversación sin customer: el nombre y la empresa salen del Lead.
    lead_only = Conversation(business_id=biz.id)
    db.add(lead_only)
    db.commit()
    db.add(
        Lead(
            business_id=biz.id,
            conversation_id=lead_only.id,
            name="Solo Lead",
            company="Comercial Solo",
        )
    )
    db.commit()
    ids["lead_only"] = lead_only.id
    db.close()
    return ids


IDS = _seed()

# FastAPI AISLADO: no se importa app.main (spec de este módulo).
app = FastAPI()
app.include_router(webhook.router)


def _override_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_db
client = TestClient(app)


@contextmanager
def _chatwoot_off():
    """Credenciales globales vacías: el comportamiento no depende del .env."""
    old_url, old_token = settings.chatwoot_url, settings.chatwoot_token
    settings.chatwoot_url = ""
    settings.chatwoot_token = ""
    try:
        yield
    finally:
        settings.chatwoot_url, settings.chatwoot_token = old_url, old_token


@contextmanager
def _token_webhook(valor: str):
    old = settings.chatwoot_token
    settings.chatwoot_token = valor
    try:
        yield
    finally:
        settings.chatwoot_token = old


def _session():
    return TestSession()


def _payload() -> HandoffPayload:
    return HandoffPayload(conversation_id=55, business_id=1, customer_name="Ana", reason="prueba")


def _estado_y_mensajes(conversation_id: int) -> tuple[str, list[Message]]:
    db = _session()
    try:
        conv = db.query(Conversation).filter_by(id=conversation_id).first()
        msgs = (
            db.query(Message)
            .filter_by(conversation_id=conversation_id)
            .order_by(Message.id)
            .all()
        )
        return (conv.state if conv else None), msgs
    finally:
        db.close()


# --- Cliente ---------------------------------------------------------------


def test_cliente_sin_configurar_no_toca_red():
    vistas: list = []
    transport = httpx.MockTransport(lambda req: vistas.append(req) or httpx.Response(200, json={}))
    c = ChatwootClient("", "tok", inbox_id=1, transport=transport)
    assert c.configured is False
    r = c.push_handoff(_payload())
    assert r == {"skipped": True, "reason": "chatwoot_not_configured"}
    assert vistas == []
    # send_message aplica el mismo guard: sin configurar, sin red y sin excepción.
    assert c.send_message(1, "hola") == {"skipped": True, "reason": "chatwoot_not_configured"}
    assert vistas == []


def test_push_ok_con_transport_200():
    peticiones: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        peticiones.append(request)
        if request.url.path.endswith("/contacts"):
            return httpx.Response(200, json={"payload": {"contact": {"id": 9}}})
        if request.url.path.endswith("/conversations"):
            return httpx.Response(200, json={"id": 42, "inbox_id": 3})
        return httpx.Response(200, json={"id": 7})

    c = ChatwootClient(
        "https://chatwoot.test", "tok", inbox_id=3, transport=httpx.MockTransport(handler)
    )
    assert c.configured is True
    r = c.push_handoff(_payload())
    assert r == {"ok": True, "conversation_id": 42}
    assert len(peticiones) == 3
    assert peticiones[0].headers["api_access_token"] == "tok"
    assert peticiones[2].url.path == "/api/v1/accounts/1/conversations/42/messages"
    cuerpo = json.loads(peticiones[2].content)
    assert cuerpo["private"] is True
    assert "Motivo: prueba" in cuerpo["content"]


def test_error_de_red_no_lanza_excepcion():
    def handler(request: httpx.Request):
        raise httpx.ConnectError("sin red", request=request)

    c = ChatwootClient(
        "https://chatwoot.test", "tok", inbox_id=1, transport=httpx.MockTransport(handler)
    )
    r = c.push_handoff(_payload())
    assert r.get("ok") is False
    assert r.get("error")


def test_error_http_no_lanza_excepcion():
    transport = httpx.MockTransport(lambda req: httpx.Response(500, json={"error": "x"}))
    c = ChatwootClient(
        "https://chatwoot.test", "tok", inbox_id=1, transport=transport
    )
    r = c.push_handoff(_payload())
    assert r == {"ok": False, "error": "http_500"}


# --- Payload y texto -------------------------------------------------------


def test_build_payload_y_format_handoff_text():
    db = _session()
    try:
        reason = "pide hablar con un humano"
        payload = build_payload(db, IDS["biz"], IDS["conv"], reason)
        assert payload.customer_name == "María López"
        assert payload.company == "ACME"
        assert payload.need == NEED
        assert payload.problem == PROBLEM
        assert payload.service_interest == SERVICE
        assert payload.budget == BUDGET
        assert payload.urgency == URGENCY
        assert payload.temperature == "caliente"
        assert payload.reason == reason
        assert len(payload.messages_tail) == 2
        assert payload.messages_tail[0]["role"] == "user"
        assert payload.messages_tail[1]["role"] == "assistant"
        # Conversation.summary vacío → resumen armado: necesidad, problema,
        # servicio de interés y próximo paso (la frase inicia con mayúscula).
        bajo = payload.summary.lower()
        for fragmento in (NEED, PROBLEM, SERVICE, NEXT):
            assert fragmento.lower() in bajo

        texto = format_handoff_text(payload)
        for etiqueta in (
            "Nombre: María López",
            "Empresa: ACME",
            f"Necesidad: {NEED}",
            f"Problema: {PROBLEM}",
            f"Servicio de interés: {SERVICE}",
            f"Presupuesto: {BUDGET}",
            f"Urgencia: {URGENCY}",
            "Temperatura: caliente",
            f"Motivo: {reason}",
            "Resumen:",
        ):
            assert etiqueta in texto
        assert "Cliente: Hola, necesito una web" in texto
        assert "Agente: ¡Hola! Contame qué necesitás." in texto
        assert texto.index("Cliente:") < texto.index("Agente:")
    finally:
        db.close()


def test_build_payload_nombre_y_empresa_desde_lead():
    db = _session()
    try:
        payload = build_payload(db, IDS["biz"], IDS["lead_only"], "sin customer")
        assert payload.customer_name == "Solo Lead"
        assert payload.company == "Comercial Solo"
    finally:
        db.close()


# --- Webhook ---------------------------------------------------------------


def test_webhook_humano_persiste_y_pasa_a_humano():
    with _chatwoot_off():
        r = client.post(
            "/webhook/chatwoot",
            json={
                "event": "message_created",
                "content": "Le respondo yo",
                "sender": {"id": 1, "name": "Vende", "type": "user"},
                "conversation_id": IDS["humano"],
            },
        )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    estado, msgs = _estado_y_mensajes(IDS["humano"])
    assert estado == "human"
    assert len(msgs) == 1
    assert msgs[0].role == "assistant"
    assert msgs[0].content == "Le respondo yo"


def test_webhook_sender_bot_no_persiste():
    with _chatwoot_off():
        r = client.post(
            "/webhook/chatwoot",
            json={
                "event": "message_created",
                "content": "Soy el bot",
                "sender": {"id": 9, "name": "Bot", "type": "agent_bot"},
                "conversation_id": IDS["bot"],
            },
        )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "ignored": True}
    estado, msgs = _estado_y_mensajes(IDS["bot"])
    assert estado == "ai"
    assert msgs == []


def test_webhook_conversacion_desconocida_404():
    with _chatwoot_off():
        r = client.post(
            "/webhook/chatwoot",
            json={
                "content": "hola",
                "sender": {"type": "user"},
                "conversation_id": 999999,
            },
        )
    assert r.status_code == 404
    assert r.json()["detail"] == "conversation_not_found"


def test_webhook_payload_real_corrige_por_atributos():
    # El id de Chatwoot (424242424) NO existe internamente: la correlación
    # viene por additional_attributes que pusimos al crear la conversación.
    with _chatwoot_off():
        r = client.post(
            "/webhook/chatwoot",
            json={
                "event": "message_created",
                "content": "Prepará la propuesta",
                "sender": {"id": 2, "name": "Vende", "type": "user"},
                "conversation": {
                    "id": 424242424,
                    "additional_attributes": {
                        "agent_ventas_conversation_id": IDS["real"],
                        "agent_ventas_business_id": IDS["biz"],
                    },
                },
            },
        )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    estado, msgs = _estado_y_mensajes(IDS["real"])
    assert estado == "human"
    assert len(msgs) == 1
    assert msgs[0].content == "Prepará la propuesta"


def test_webhook_sender_type_top_level():
    with _chatwoot_off():
        r = client.post(
            "/webhook/chatwoot",
            json={
                "sender_type": "human",
                "content": "Hola desde Chatwoot",
                "conversation_id": IDS["tip"],
            },
        )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    estado, msgs = _estado_y_mensajes(IDS["tip"])
    assert estado == "human"
    assert len(msgs) == 1


def test_webhook_verificacion_con_token():
    with _token_webhook("secreto-webhook"):
        sin_credencial = client.post(
            "/webhook/chatwoot",
            json={"content": "x", "sender": {"type": "user"}, "conversation_id": IDS["verif"]},
        )
        assert sin_credencial.status_code == 401
        con_query = client.post(
            "/webhook/chatwoot?token=secreto-webhook",
            json={"content": "entro", "sender": {"type": "user"}, "conversation_id": IDS["verif"]},
        )
        assert con_query.status_code == 200
        assert con_query.json() == {"ok": True}
    estado, msgs = _estado_y_mensajes(IDS["verif"])
    assert estado == "human"
    assert len(msgs) == 1


# --- Aislamiento de tenant -------------------------------------------------


def test_push_handoff_for_business_negocio_sin_chatwoot():
    db = _session()
    try:
        with _chatwoot_off():
            r = push_handoff_for_business(db, IDS["biz"], IDS["conv"], "x")
        assert r == {"skipped": True, "reason": "chatwoot_not_configured"}
        ausente = push_handoff_for_business(db, 999999, IDS["conv"], "x")
        assert ausente == {"ok": False, "error": "business_not_found"}
    finally:
        db.close()
