"""H20: el CRM por token de negocio aísla los datos entre tenants.

Crea dos businesses con leads y citas distintos, autentica con el
`Business.crm_token` de uno y confirma que los datos del otro NUNCA
aparecen en la respuesta — leads Y citas, ambos endpoints.
"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db.database import get_db
from app.db.models import Appointment, Base, Business, Conversation, Lead
from app.main import app

engine = create_engine(
    "sqlite:////tmp/opencode/crm_tenant_test.db", connect_args={"check_same_thread": False}
)
Test = sessionmaker(bind=engine, autoflush=False, autocommit=False)

TOKEN_A = "crm-token-aaa-111"
TOKEN_B = "crm-token-bbb-222"


def _seed():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = Test()
    a = Business(name="Hotel Miramar", description="d", crm_token=TOKEN_A)
    b = Business(name="Cafe Central", description="d", crm_token=TOKEN_B)
    db.add_all([a, b])
    db.commit()

    ca = Conversation(business_id=a.id, channel="web", source="widget")
    cb = Conversation(business_id=b.id, channel="web", source="widget")
    db.add_all([ca, cb])
    db.commit()

    db.add(
        Lead(
            business_id=a.id,
            conversation_id=ca.id,
            name="Alice Hotel",
            phone="+54 9 11 0001",
            status="qualified",
            last_interaction=datetime.utcnow(),
        )
    )
    db.add(
        Lead(
            business_id=b.id,
            conversation_id=cb.id,
            name="Bruno Cafe",
            phone="+54 9 11 0002",
            status="qualified",
            last_interaction=datetime.utcnow(),
        )
    )
    db.add(
        Appointment(
            business_id=a.id,
            conversation_id=ca.id,
            start=datetime.utcnow() + timedelta(days=1),
            customer_name="Alice Hotel",
            contact="+54 9 11 0001",
        )
    )
    db.add(
        Appointment(
            business_id=b.id,
            conversation_id=cb.id,
            start=datetime.utcnow() + timedelta(days=2),
            customer_name="Bruno Cafe",
            contact="+54 9 11 0002",
        )
    )
    db.commit()
    ids = (a.id, b.id)
    db.close()
    return ids


BIZ_A, BIZ_B = _seed()


def _override_db():
    db = Test()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _crm_db():
    """Override de BD + token global fuera de escena: solo manda el de tenant."""
    from app.main import app as _app

    prev = _app.dependency_overrides.get(get_db)
    _app.dependency_overrides[get_db] = _override_db
    old_token = settings.crm_token
    settings.crm_token = ""
    yield
    settings.crm_token = old_token
    if prev is None:
        _app.dependency_overrides.pop(get_db, None)
    else:
        _app.dependency_overrides[get_db] = prev


client = TestClient(app)


def test_tenant_token_a_nunca_ve_datos_de_b():
    r = client.get("/api/leads", params={"token": TOKEN_A})
    assert r.status_code == 200
    leads = r.json()["leads"]
    assert leads, "el tenant A sí ve sus propios leads"
    names = {l["name"] for l in leads}
    assert "Alice Hotel" in names
    assert "Bruno Cafe" not in names
    # ni por campo suelto: el teléfono/lead de B no aparece en el JSON crudo
    assert "Bruno" not in r.text
    assert "+54 9 11 0002" not in r.text
    assert all(l["conversation_id"] is not None for l in leads)


def test_tenant_token_b_nunca_ve_datos_de_a_en_citas():
    r = client.get("/api/appointments", params={"token": TOKEN_B})
    assert r.status_code == 200
    appts = r.json()["appointments"]
    assert appts, "el tenant B sí ve sus propias citas"
    assert all(a["customer_name"] == "Bruno Cafe" for a in appts)
    assert "Alice" not in r.text
    assert "+54 9 11 0001" not in r.text
    assert all(
        a["start"] < (datetime.utcnow() + timedelta(days=3)).isoformat() for a in appts
    )


def test_tenant_token_a_tampoco_ve_citas_de_b():
    r = client.get("/api/appointments", params={"token": TOKEN_A})
    assert r.status_code == 200
    assert all(a["customer_name"] == "Alice Hotel" for a in r.json()["appointments"])
    assert "Bruno" not in r.text


def test_token_invalido_401_y_sin_token_401():
    assert client.get("/api/leads", params={"token": "no-existe"}).status_code == 401
    assert client.get("/api/appointments", params={"token": ""}).status_code == 401


def test_token_de_operador_ve_los_dos_tenants():
    old = settings.crm_token
    settings.crm_token = "operador-global"
    try:
        r = client.get("/api/leads", params={"token": "operador-global"})
        assert r.status_code == 200
        names = {l["name"] for l in r.json()["leads"]}
        assert {"Alice Hotel", "Bruno Cafe"} <= names
    finally:
        settings.crm_token = old
