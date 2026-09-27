"""Seed de Sereno: marca, planes exactos de la landing e idempotencia."""
import re

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Product
from app.db.seed_demo import SERENO_PLANS, _seed_business
from app.rag.seed_knowledge import SERENO_KB

# El verifier (H1/H9) bloquea respuestas con estos patrones; el texto de Sereno
# que Ana repite no puede traerlos ni montos fuera de catálogo.
_PROMO_RE = re.compile(r"oferta|promoci|descuento|rebaja|\bdto\b", re.I)
_AMOUNT_RE = re.compile(r"\$\s?[\d.,]+|\d+\s?(?:usd|mxn|pesos|€)", re.I)


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_seed_sereno_planes_con_precios_de_la_landing():
    db = _session()
    biz = _seed_business(db, "Sereno", "Landing pages + chatbot de IA.", SERENO_PLANS,
                         persona="experta")
    got = {p.name: p.price_cents for p in db.query(Product).filter_by(business_id=biz.id)}
    assert got == {
        "Recepción 24/7": 14900,
        "Recepción + Closer": 29900,
        "Chatbot IA mensual": 9900,
    }

    # Idempotente: un segundo seed no duplica productos.
    _seed_business(db, "Sereno", "Landing pages + chatbot de IA.", SERENO_PLANS,
                   persona="experta")
    assert db.query(Product).filter_by(business_id=biz.id).count() == 3
    db.close()


def test_textos_sereno_sin_promo_ni_montos():
    """Ana repite estos textos; el verifier rechazaría la respuesta si traen
    palabras promo o montos $ que no coincidan con el catálogo."""
    texts = [desc for _, desc, _ in SERENO_PLANS]
    texts += [content for _, _, content, _ in SERENO_KB]
    texts += [keywords for _, _, _, keywords in SERENO_KB]
    for text in texts:
        assert not _PROMO_RE.search(text), text
        assert not _AMOUNT_RE.search(text), text
