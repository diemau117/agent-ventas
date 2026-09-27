"""SalesMind: memoria de cliente, asesora fija, calendario, montos exactos."""
from datetime import datetime, timedelta

from app.agent.verifier import verify_response
from app.db.models import Appointment, Business, Conversation, Customer, Product
from app.services.customer import ADVISORS, assign_advisor


def _db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.models import Base

    e = create_engine("sqlite:////tmp/opencode/salesmind_test.db", connect_args={"check_same_thread": False})
    Base.metadata.drop_all(e)
    Base.metadata.create_all(e)
    return sessionmaker(bind=e)()


def _biz(db, name="SM"):
    b = Business(name=name, description="d")
    db.add(b)
    db.commit()
    return b


def test_advisor_fixed_and_distributed():
    seen = {assign_advisor(i) for i in range(30)}
    assert seen == set(ADVISORS)
    assert assign_advisor(7) == assign_advisor(7)


def test_customer_profile_roundtrip():
    from app.tools.registry import execute_tool

    db = _db()
    b = _biz(db)
    conv = Conversation(business_id=b.id)
    db.add(conv)
    db.commit()
    from app.services.customer import ensure_customer, profile_text

    c = ensure_customer(db, b.id, conv)
    assert c.advisor_name in ADVISORS and conv.customer_id == c.id
    assert profile_text(c) == ""
    r = execute_tool(db, b.id, conv.id, "update_customer",
                     {"name": "Martín", "phone": "099123456",
                      "facts": {"rubro": "panadería", "dolor": "pierde pedidos por WhatsApp"}})
    assert r["name"] == "Martín" and r["facts"]["rubro"] == "panadería"
    assert "Martín" in profile_text(db.query(Customer).filter_by(id=c.id).first())


def test_appointment_flow_and_overlap():
    from app.tools.registry import execute_tool, _free_slots

    db = _db()
    b = _biz(db)
    conv = Conversation(business_id=b.id)
    db.add(conv)
    db.commit()
    assert execute_tool(db, b.id, conv.id, "create_appointment", {"start": "no-fecha"}) == {"error": "invalid_slot"}
    assert execute_tool(db, b.id, conv.id, "create_appointment", {"start": "2020-01-01T10:00:00"}) == {"error": "invalid_slot"}
    slots = _free_slots(db, b.id)
    assert len(slots) == 6
    ok = execute_tool(db, b.id, conv.id, "create_appointment",
                      {"start": slots[0], "name": "Martín", "contact": "099"})
    assert ok["status"] == "confirmed"
    assert execute_tool(db, b.id, conv.id, "create_appointment", {"start": slots[0]}) == {"error": "slot_taken"}
    assert slots[0] not in _free_slots(db, b.id)


def test_appointment_claim_requires_backend_confirmation():
    v = verify_response("Dale, te agendé la llamada para el lunes a las 10", {})
    assert not v["approved"] and "appointment_unconfirmed" in v["issues"]
    v2 = verify_response("Listo, agendada para el lunes 10h",
                         {"create_appointment": {"appointment_id": 3, "status": "confirmed"}})
    assert v2["approved"]


def test_price_mismatch_rejected_exact_accepted():
    ctx = {"search_products": {"products": [{"name": "Plan", "price_cents": 29900}]}}
    assert not verify_response("Cuesta $250", ctx)["approved"]
    assert verify_response("Cuesta $299.00 USD", ctx)["approved"]
    assert verify_response("Cuesta $299", ctx)["approved"]


def test_show_plans_returns_catalog_as_cards():
    from app.services.conversation import _cards
    from app.tools.registry import execute_tool

    db = _db()
    b = _biz(db)
    conv = Conversation(business_id=b.id)
    db.add(conv)
    db.commit()
    db.add(Product(business_id=b.id, name="Plan X", description="desc", price_cents=29900))
    db.commit()
    res = execute_tool(db, b.id, conv.id, "show_plans", {})
    assert res["products"] and res["products"][0]["name"] == "Plan X"
    cards = _cards({"show_plans": res})
    assert cards[0]["price_label"] == "$299.00 USD"
    assert cards[0]["cta"] == "Me interesa el plan Plan X"
    # El verificador acepta montos respaldados por show_plans.
    assert verify_response("Cuesta $299", {"show_plans": res})["approved"]
    assert not verify_response("Cuesta $250", {"show_plans": res})["approved"]


def test_tenant_isolation_new_tables():
    from app.tools.registry import execute_tool

    db = _db()
    a, b = _biz(db, "A"), _biz(db, "B")
    db.add(Product(business_id=a.id, name="Plan A", price_cents=100))
    ca, cb = Conversation(business_id=a.id), Conversation(business_id=b.id)
    db.add_all([ca, cb])
    db.commit()
    assert execute_tool(db, b.id, cb.id, "search_products", {"query": "Plan"}) == {"products": []}
    assert execute_tool(db, b.id, cb.id, "get_customer", {}) == {"error": "no_customer"}
    slot = execute_tool(db, a.id, ca.id, "check_availability", {})["slots"][0]
    execute_tool(db, a.id, ca.id, "create_appointment", {"start": slot})
    assert slot in execute_tool(db, b.id, cb.id, "check_availability", {})["slots"]
