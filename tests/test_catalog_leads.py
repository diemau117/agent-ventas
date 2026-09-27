"""Rutas de la landing: catálogo por public_key y captación de leads (§18)."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import catalog, leads
from app.db.database import get_db
from app.db.models import Base, Business, Lead, Product

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(engine)
TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _seed():
    db = TestSession()
    try:
        a = Business(
            name="Negocio A",
            description="Descripción de A.",
            agent_name="Sofi",
            public_key="pk_alpha",
            hours="Lun a Vie 9-18",
            phone="+54 11 1111 2222",
            whatsapp="+54 11 1111 2222",
        )
        b = Business(name="Negocio B", public_key="pk_beta")
        empty = Business(name="Vacío", public_key="pk_empty")
        db.add_all([a, b, empty])
        db.flush()
        db.add_all(
            [
                # A tiene categorías comerciales: plans + services.
                Product(
                    business_id=a.id,
                    name="Plan Alpha",
                    description="Todo incluido.",
                    price_cents=15000,
                    currency="USD",
                    category="plan",
                ),
                Product(
                    business_id=a.id,
                    name="Asesoría",
                    description="Sesión de consultoría.",
                    price_cents=5000,
                    currency="USD",
                    category="servicio",
                ),
                # B solo tiene "producto": fallback → todo activo en plans.
                Product(
                    business_id=b.id,
                    name="Kit B",
                    description="Producto genérico de B.",
                    price_cents=9900,
                    currency="USD",
                    category="producto",
                ),
            ]
        )
        db.commit()
        return a.id, b.id
    finally:
        db.close()


BIZ_A, BIZ_B = _seed()

app = FastAPI()
app.include_router(catalog.router)
app.include_router(leads.router)


def _override_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_db
client = TestClient(app)


def test_catalog_valid_key_returns_business_and_plans():
    r = client.get("/catalog", params={"public_key": "pk_alpha"})
    assert r.status_code == 200
    body = r.json()
    assert body["business"]["name"] == "Negocio A"
    assert body["business"]["agent_name"] == "Sofi"
    assert body["business"]["hours"] == "Lun a Vie 9-18"
    # Nunca se filtra el identificador interno del tenant.
    assert "business_id" not in body["business"] and "id" not in body["business"]
    names = [p["name"] for p in body["plans"]]
    assert names == ["Plan Alpha"]
    assert body["plans"][0]["price_label"] == "$150.00 USD"
    assert body["plans"][0]["category"] == "plan"
    assert [s["name"] for s in body["services"]] == ["Asesoría"]
    assert body["services"][0]["price_label"] == "$50.00 USD"


def test_catalog_invalid_key_returns_401():
    r = client.get("/catalog", params={"public_key": "no-existe"})
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid_public_key"
    # Sin clave también es inválida, no un 500 ni un listado abierto.
    assert client.get("/catalog").status_code == 401


def test_catalog_business_without_products_returns_empty_plans():
    r = client.get("/catalog", params={"public_key": "pk_empty"})
    assert r.status_code == 200
    body = r.json()
    assert body["plans"] == []
    assert body["services"] == []


def test_catalog_without_commercial_categories_falls_back_to_all():
    r = client.get("/catalog", params={"public_key": "pk_beta"})
    assert r.status_code == 200
    body = r.json()
    assert [p["name"] for p in body["plans"]] == ["Kit B"]
    assert body["services"] == []


def test_catalog_isolates_tenants_by_public_key():
    a = client.get("/catalog", params={"public_key": "pk_alpha"}).json()
    b = client.get("/catalog", params={"public_key": "pk_beta"}).json()
    a_names = [p["name"] for p in a["plans"]] + [s["name"] for s in a["services"]]
    b_names = [p["name"] for p in b["plans"]] + [s["name"] for s in b["services"]]
    assert "Kit B" not in a_names
    assert "Plan Alpha" not in b_names
    assert b["business"]["name"] == "Negocio B"


def test_lead_created_from_landing():
    r = client.post(
        "/leads",
        json={
            "public_key": "pk_alpha",
            "name": "  Juan Pérez ",
            "company": "Pérez SA",
            "email": "juan@ejemplo.com",
            "phone": "",
            "message": "Necesito una landing con agente.",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert isinstance(body["lead_id"], int)
    assert body["status"] == "new"
    assert "business_id" not in body

    db = TestSession()
    try:
        lead = db.query(Lead).filter_by(id=body["lead_id"]).first()
        assert lead is not None
        assert lead.business_id == BIZ_A
        assert lead.source == "landing"
        assert lead.conversation_id is None
        assert lead.name == "Juan Pérez"
        assert lead.email == "juan@ejemplo.com"
        # need toma message cuando need viene vacío.
        assert lead.need == "Necesito una landing con agente."
    finally:
        db.close()


def test_lead_requires_at_least_one_contact_field():
    r = client.post(
        "/leads",
        json={
            "public_key": "pk_alpha",
            "name": "   ",
            "email": "",
            "phone": "",
            "message": "Hola, quiero información.",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "contact_required"

    db = TestSession()
    try:
        sin_contacto = (
            db.query(Lead)
            .filter(Lead.name == "", Lead.email == "", Lead.phone == "")
            .count()
        )
        assert sin_contacto == 0
    finally:
        db.close()


def test_lead_with_invalid_public_key_returns_401():
    r = client.post(
        "/leads",
        json={"public_key": "no-existe", "name": "Ana", "email": "ana@x.com"},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid_public_key"
