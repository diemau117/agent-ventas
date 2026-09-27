"""CRM: lead estructurado + temperatura (spec §11-12)."""

from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.crm import (
    apply_temperature,
    extract_contact,
    lead_brief,
    record_interaction,
    score_temperature,
    signals_from_conversation,
    upsert_lead,
)
from app.db.models import Appointment, Base, Business, Conversation, Lead, Message


def _db():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.drop_all(e)
    Base.metadata.create_all(e)
    return sessionmaker(bind=e)()


def _biz(db, name="CRM"):
    b = Business(name=name, description="d")
    db.add(b)
    db.commit()
    return b


def _conv(db, b):
    c = Conversation(business_id=b.id, channel="web", source="widget")
    db.add(c)
    db.commit()
    return c


def test_upsert_idempotente_no_pisa_con_vacios():
    db = _db()
    b = _biz(db)
    c = _conv(db, b)

    l1 = upsert_lead(db, b.id, c.id, name="María", need="Sitio web", urgency="")
    l2 = upsert_lead(db, b.id, c.id, name="", need="", urgency="48h")

    assert l1.id == l2.id
    assert db.query(Lead).filter_by(business_id=b.id).count() == 1
    # Los vacíos no pisan lo existente; el campo nuevo sí se aplica.
    assert l2.name == "María"
    assert l2.need == "Sitio web"
    assert l2.urgency == "48h"


def test_upsert_aislamiento_por_business():
    db = _db()
    b1 = _biz(db, "Negocio 1")
    b2 = _biz(db, "Negocio 2")
    c = _conv(db, b1)

    # Mismo conversation_id en dos negocios: no deben colisionar.
    lead1 = upsert_lead(db, b1.id, c.id, name="Ana")
    lead2 = upsert_lead(db, b2.id, c.id, name="Bruno")

    assert lead1.id != lead2.id
    assert db.query(Lead).filter_by(business_id=b1.id).count() == 1
    assert db.query(Lead).filter_by(business_id=b2.id).count() == 1
    assert lead1.name == "Ana"
    assert lead2.name == "Bruno"


def test_extract_contact():
    assert extract_contact("maria@acme.com") == ("maria@acme.com", "")
    assert extract_contact("+54 9 11 5555-1234") == ("", "+54 9 11 5555-1234")
    assert extract_contact("") == ("", "")


def test_lead_brief_refleja_y_omite_vacios():
    db = _db()
    b = _biz(db)
    c = _conv(db, b)
    lead = upsert_lead(db, b.id, c.id, name="María", need="Rediseñar web", urgency="Alta")

    brief = lead_brief(lead)
    assert "Nombre: María" in brief
    assert "Necesidad: Rediseñar web" in brief
    assert "Urgencia: Alta" in brief
    assert "Temperatura: frio" in brief
    # Campos vacíos no aparecen.
    assert "Empresa" not in brief
    assert "Presupuesto" not in brief
    assert "Próximo paso" not in brief


def test_record_interaction_no_pisa_con_vacios():
    db = _db()
    b = _biz(db)
    c = _conv(db, b)
    lead = upsert_lead(db, b.id, c.id, name="María", need="Rediseñar web")

    updated = record_interaction(db, lead, summary="Habló por chat")
    assert updated.last_interaction is not None
    assert updated.ai_summary == "Habló por chat"
    assert updated.name == "María"
    assert updated.need == "Rediseñar web"

    before = updated.last_interaction
    again = record_interaction(db, lead, need="", name="María G.")
    assert again.need == "Rediseñar web"
    assert again.name == "María G."
    assert again.last_interaction >= before


def test_score_temperature_una_asercion_por_rama():
    assert score_temperature({"converted": True}) == "cliente"
    assert score_temperature({"has_contact": True, "ready_to_buy": True}) == "calificado"
    assert score_temperature({"has_contact": True, "has_appointment": True}) == "calificado"
    assert score_temperature({"has_contact": True}) == "caliente"
    assert score_temperature({"asked_price": True}) == "caliente"
    assert score_temperature({"stated_problem": True}) == "caliente"
    assert score_temperature({"has_budget": True}) == "tibio"
    assert score_temperature({"message_count": 4}) == "tibio"
    assert score_temperature({}) == "frio"


def test_apply_temperature_persiste():
    db = _db()
    b = _biz(db)
    c = _conv(db, b)
    lead = upsert_lead(db, b.id, c.id, name="María")

    apply_temperature(db, lead, {"converted": True})
    assert db.query(Lead).filter_by(id=lead.id).first().temperature == "cliente"


def test_signals_detecta_precio_y_contacto():
    db = _db()
    b = _biz(db)
    c = _conv(db, b)

    db.add(Message(conversation_id=c.id, role="user", content="¿Cuánto cuesta el plan Pro?"))
    db.add(Message(conversation_id=c.id, role="assistant", content="Te paso la info"))
    db.commit()

    s = signals_from_conversation(db, b.id, c.id)
    # "cuánto" acentuado debe detectarse igual (normalización).
    assert s["asked_price"] is True
    assert s["message_count"] == 2
    assert s["has_contact"] is False

    upsert_lead(db, b.id, c.id, email="maria@acme.com")
    s2 = signals_from_conversation(db, b.id, c.id)
    assert s2["has_contact"] is True


def test_signals_no_cruza_negocios():
    db = _db()
    b1 = _biz(db, "N1")
    b2 = _biz(db, "N2")
    c = _conv(db, b1)
    db.add(Message(conversation_id=c.id, role="user", content="necesito ayuda"))
    db.commit()

    assert signals_from_conversation(db, b1.id, c.id)["stated_problem"] is True
    assert signals_from_conversation(db, b2.id, c.id)["message_count"] == 0


def test_signals_appointment_y_urgencia():
    db = _db()
    b = _biz(db)
    c = _conv(db, b)
    db.add(
        Appointment(
            business_id=b.id,
            conversation_id=c.id,
            customer_name="María",
            contact="maria@acme.com",
            start=datetime.now() + timedelta(days=1),
            status="confirmed",
        )
    )
    db.add(Message(conversation_id=c.id, role="user", content="Urgente, es para esta semana"))
    db.commit()

    s = signals_from_conversation(db, b.id, c.id)
    assert s["has_appointment"] is True
    assert s["has_urgency"] is True
    # Con appointment + urgencia pero sin contacto: cae en tibio por urgencia.
    assert score_temperature(s) == "tibio"


def test_ready_to_buy_lo_aporta_el_orquestador():
    """CLOSE/CAPTURE_CONTACT + contacto → calificado (scoring §12)."""
    db = _db()
    b = _biz(db)
    c = _conv(db, b)

    # Sin ready_to_buy: contacto solo da "caliente".
    upsert_lead(db, b.id, c.id, phone="3515555888")
    s = signals_from_conversation(db, b.id, c.id)
    assert s["ready_to_buy"] is False
    assert score_temperature(s) == "caliente"

    # El orquestador marca intención de compra (Jeff CLOSE) → "calificado".
    s2 = signals_from_conversation(db, b.id, c.id, ready_to_buy=True)
    assert s2["ready_to_buy"] is True
    assert score_temperature(s2) == "calificado"
