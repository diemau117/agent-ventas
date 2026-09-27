"""Tests del módulo RAG: retrieval, contexto, categorías y seed idempotente."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Business, Knowledge
from app.rag.retriever import categories_for, knowledge_context, retrieve
from app.rag.seed_knowledge import seed_knowledge


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _biz(db, name):
    biz = Business(name=name, description=name)
    db.add(biz)
    db.commit()
    db.refresh(biz)
    return biz


def _doc(db, business_id, title, content, category="general", keywords="", active=True):
    doc = Knowledge(
        business_id=business_id,
        title=title,
        content=content,
        category=category,
        keywords=keywords,
        active=active,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def test_retrieve_aislamiento_por_negocio():
    db = _session()
    b1 = _biz(db, "Negocio Uno")
    b2 = _biz(db, "Negocio Dos")
    _doc(db, b1.id, "Horario del local", "Abrimos de 9 a 18.", category="horario")
    _doc(db, b2.id, "Horario de soporte", "Soporte de 10 a 20.", category="horario")

    results = retrieve(db, b1.id, "horario")
    assert len(results) == 1
    assert results[0]["title"] == "Horario del local"
    db.close()


def test_retrieve_excluye_inactivos():
    db = _session()
    biz = _biz(db, "Negocio")
    _doc(db, biz.id, "Política vieja", "Esta política ya no aplica.", active=False)

    assert retrieve(db, biz.id, "política vieja") == []
    assert knowledge_context(db, biz.id, "política vieja") == ""
    db.close()


def test_retrieve_sin_matches_devuelve_vacio_y_contexto_vacio():
    db = _session()
    biz = _biz(db, "Negocio")
    _doc(db, biz.id, "Horario", "Lunes a viernes de 9 a 18.", category="horario")

    assert retrieve(db, biz.id, "zorro volador espacial") == []
    assert knowledge_context(db, biz.id, "zorro volador espacial") == ""
    db.close()


def test_scoring_ordena_lo_mas_relevante_primero():
    db = _session()
    biz = _biz(db, "Negocio")
    _doc(
        db,
        biz.id,
        "Envíos a todo el país",
        "Enviamos en 48 horas con Andreani.",
        category="politica",
        keywords="envio entrega",
    )
    _doc(
        db,
        biz.id,
        "Política de privacidad",
        "Usamos tus datos sólo para procesar el pedido y el envío del mismo.",
        category="politica",
    )

    results = retrieve(db, biz.id, "¿cuánto demora el envío?")
    assert results
    assert results[0]["title"] == "Envíos a todo el país"
    assert results[0]["score"] >= results[-1]["score"]
    db.close()


def test_knowledge_context_inyecta_front_y_categoria():
    db = _session()
    biz = _biz(db, "Negocio")
    _doc(db, biz.id, "Horario", "Lunes a viernes de 9 a 18.", category="horario")

    ctx = knowledge_context(db, biz.id, "horario")
    assert ctx.startswith("Información de la empresa (fuente autorizada):")
    assert "[horario] Horario: Lunes a viernes de 9 a 18." in ctx
    db.close()


def test_categories_for_filtra_por_categoria():
    db = _session()
    biz = _biz(db, "Negocio")
    h = _doc(db, biz.id, "Horario", "9 a 18.", category="horario")
    d = _doc(db, biz.id, "Dirección", "Calle 123.", category="direccion")
    _doc(db, biz.id, "FAQ", "Pregunta frecuente.", category="faq")
    _doc(db, biz.id, "Dirección inactiva", "No existe.", category="direccion", active=False)

    results = categories_for(db, biz.id, ("horario", "direccion"))
    ids = {r["id"] for r in results}
    assert ids == {h.id, d.id}
    assert all(r["category"] in ("horario", "direccion") for r in results)
    db.close()


def test_seed_knowledge_idempotente():
    db = _session()
    _biz(db, "Tienda Demo")
    _biz(db, "Sereno")

    first = seed_knowledge(db)
    assert first > 0
    total_after_first = db.query(Knowledge).count()

    second = seed_knowledge(db)
    assert second == 0
    assert db.query(Knowledge).count() == total_after_first

    # Categorías todas válidas y ambos negocios con docs.
    from app.db.models import KNOWLEDGE_CATEGORIES

    docs = db.query(Knowledge).all()
    assert all(d.category in KNOWLEDGE_CATEGORIES for d in docs)
    biz_ids = {d.business_id for d in docs}
    assert len(biz_ids) == 2
    db.close()
