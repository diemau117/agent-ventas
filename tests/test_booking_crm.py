"""CRM ↔ Calendario end-to-end: agendar → cita ligada → lead avanza → humano la ve.

Flujo real (spec §10-12): el cliente pide agendar, el LLM ofrece slots
(`check_availability`), confirma y se crea el `Appointment` **ligado al lead**
(`lead_id`), el lead avanza `new/working → qualified` con `next_action`, y el
humano lo ve en `GET /api/appointments` con `CRM_TOKEN`.
"""
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes import chat as chat_route
from app.db.database import get_db
from app.db.models import Appointment, Base, Business, Conversation, Customer, Lead, Product
from app.llm.base import FakeProvider
from app.main import app

engine = create_engine(
    "sqlite:////tmp/opencode/booking_crm_test.db", connect_args={"check_same_thread": False}
)
Test = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _seed():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = Test()
    b = Business(name="A", description="d")
    db.add(b)
    db.commit()
    db.add(Product(business_id=b.id, name="Café A", description="café", price_cents=1000))
    db.commit()
    ids = (b.id, b.public_key)
    db.close()
    return ids


BIZ_ID, KEY = _seed()


def _override_db():
    db = Test()
    try:
        yield db
    finally:
        db.close()


# Override por test (no por import): test_api.py también fija get_db y este
# módulo se importa después — pisarlo a nivel de módulo rompería sus 401.
import pytest


@pytest.fixture(autouse=True)
def _crm_db():
    from app.main import app as _app

    prev = _app.dependency_overrides.get(get_db)
    _app.dependency_overrides[get_db] = _override_db
    yield
    if prev is None:
        _app.dependency_overrides.pop(get_db, None)
    else:
        _app.dependency_overrides[get_db] = prev


chat_route._llm = FakeProvider()
client = TestClient(app)


def _post(msg, conv=None):
    body = {"public_key": KEY, "message": msg}
    if conv:
        body["conversation_id"] = conv
    return client.post("/api/chat", json=body)


def test_agendar_crea_cita_ligada_al_lead_y_avanza():
    # Turno 1: pide agendar → check_availability con slots repartidos por días.
    r1 = _post("Agendamos una llamada para ver la demo")
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["next_step"] in ("SCHEDULE", "CLOSE", "CAPTURE_CONTACT", "FOLLOW_UP", "DISCOVER")
    conv = d1["conversation_id"]

    # Turno 2: confirma → create_appointment.
    r2 = _post("Sí, dale, perfecto", conv)
    assert r2.status_code == 200

    db = Test()
    appt = db.query(Appointment).filter_by(business_id=BIZ_ID).order_by(Appointment.id.desc()).first()
    assert appt is not None, "la confirmación debía crear el Appointment"
    assert appt.conversation_id == conv
    # Cita ligada al lead de la conversación.
    lead = db.query(Lead).filter_by(business_id=BIZ_ID, conversation_id=conv).first()
    assert lead is not None
    assert appt.lead_id == lead.id, "la cita debe apuntar al lead (CRM §10)"
    # El lead avanza de estado y queda con próximo paso.
    assert lead.status == "qualified", f"lead debía avanzar a qualified, quedó {lead.status}"
    assert "Cita agendada" in lead.next_action
    # Contexto del vendedor en la cita (spec §10).
    assert appt.start > datetime.now()
    assert appt.status == "confirmed"
    db.close()


def test_slots_day_aware_cobren_la_semana():
    """Los slots se reparten por el horizonte: alcanza el martes, no solo hoy."""
    from app.tools.registry import _free_slots

    db = Test()
    slots = _free_slots(db, BIZ_ID, days=7, limit=6)
    db.close()
    assert len(slots) == 6
    days = {s[:10] for s in slots}
    assert len(days) >= 3, f"los slots deben repartirse en varios días, salió {days}"
    # Todos futuros, hora hábil 10:00/15:00, lun-vie.
    for s in slots:
        dt = datetime.fromisoformat(s)
        assert dt > datetime.now()
        assert dt.weekday() < 5
        assert dt.hour in (10, 15) and dt.minute == 0


def test_get_appointments_requiere_token_y_muestra_cita():
    # Sin token → 401/403 (deshabilitado o inválido), nunca filtra PII.
    assert client.get("/api/appointments").status_code in (401, 403)
    assert client.get("/api/appointments", params={"token": "malo"}).status_code in (401, 403)

    from app.config import settings

    old = settings.crm_token
    settings.crm_token = "tok-test-123"
    try:
        r = client.get("/api/appointments", params={"token": "tok-test-123"})
        assert r.status_code == 200
        data = r.json()
        assert data["appointments"], "el humano debe ver la cita creada"
        appt = data["appointments"][0]
        assert appt["lead_id"] is not None
        assert appt["status"] == "confirmed"

        # Leads visibles con el mismo token, filtrables por status.
        rl = client.get("/api/leads", params={"token": "tok-test-123", "status": "qualified"})
        assert rl.status_code == 200
        assert any(l["status"] == "qualified" for l in rl.json()["leads"])
    finally:
        settings.crm_token = old


def test_crm_deshabilitado_sin_token_configurado():
    from app.config import settings

    old = settings.crm_token
    settings.crm_token = ""
    try:
        r = client.get("/api/leads", params={"token": "cualquiera"})
        assert r.status_code == 403
        assert r.json()["detail"] == "crm_disabled"
    finally:
        settings.crm_token = old


def test_handoff_incluye_proxima_cita():
    from app.integrations.chatwoot import build_payload

    db = Test()
    appt = db.query(Appointment).filter_by(business_id=BIZ_ID).order_by(Appointment.id.desc()).first()
    if appt is None:
        db.add(
            Appointment(
                business_id=BIZ_ID,
                start=datetime.now() + timedelta(days=1),
                status="confirmed",
            )
        )
        db.commit()
        appt = db.query(Appointment).filter_by(business_id=BIZ_ID).order_by(Appointment.id.desc()).first()
    payload = build_payload(db, BIZ_ID, appt.conversation_id, "prueba")
    db.close()
    assert payload.appointment, "el handoff debe llevar la próxima cita"
    from app.integrations.chatwoot import format_handoff_text

    assert "Próxima cita:" in format_handoff_text(payload)
