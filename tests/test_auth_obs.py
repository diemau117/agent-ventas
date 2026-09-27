"""Tests del slice Auth/Observability: clave pública, presupuesto diario,
rate limit persistente y logging estructurado (H4, spec §21)."""
import io
import json
import logging
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import (
    PUBLIC_KEY_HEADER,
    check_daily_budget,
    enforce_budget,
    resolve_business,
)
from app.config import settings
from app.db.models import Base, Business, Conversation, EventLog, Message, RateLimitBucket
from app.middleware import RateLimitMiddleware, install_middlewares
from app.observability import (
    JsonFormatter,
    configure_logging,
    log_event,
    log_turn,
    new_request_id,
)


@pytest.fixture()
def engine():
    e = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(e)
    yield e
    e.dispose()


@pytest.fixture()
def db(engine):
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    yield session
    session.close()


@pytest.fixture()
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _biz(db, **kw):
    b = Business(name=kw.pop("name", "ACME"), **kw)
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def _seed_messages(db, business, tokens_in, tokens_out, created):
    conv = Conversation(business_id=business.id)
    db.add(conv)
    db.flush()
    db.add(
        Message(
            conversation_id=conv.id,
            role="assistant",
            content="x",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            created=created,
        )
    )
    db.commit()
    return conv


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _who_app(db):
    app = FastAPI()

    @app.get("/who")
    def who(request: Request):
        business = resolve_business(request, db)
        return {"id": business.id}

    return app


# ---------------------------------------------------------------- resolve_business


def test_resolve_business_valid_header_key(db):
    b = _biz(db)
    client = TestClient(_who_app(db))
    r = client.get("/who", headers={PUBLIC_KEY_HEADER: b.public_key})
    assert r.status_code == 200
    assert r.json() == {"id": b.id}


def test_resolve_business_invalid_key_401(db):
    _biz(db)
    client = TestClient(_who_app(db))
    r = client.get("/who", headers={PUBLIC_KEY_HEADER: "clave-mala"})
    assert r.status_code == 401
    assert r.json() == {"detail": "invalid_public_key"}


def test_resolve_business_missing_key_401(db):
    _biz(db)
    client = TestClient(_who_app(db))
    assert client.get("/who").status_code == 401
    # sin header tampoco sirve un public_key vacío
    assert client.get("/who?public_key=").status_code == 401


def test_resolve_business_query_param_fallback(db):
    b = _biz(db)
    client = TestClient(_who_app(db))
    r = client.get(f"/who?public_key={b.public_key}")
    assert r.status_code == 200
    assert r.json() == {"id": b.id}
    # el header tiene precedencia sobre el query param
    r2 = client.get(f"/who?public_key={b.public_key}", headers={PUBLIC_KEY_HEADER: "mala"})
    assert r2.status_code == 401


# ---------------------------------------------------------------- check_daily_budget


def test_check_daily_budget_under_and_over(db):
    now = _utcnow()
    yesterday = now - timedelta(days=1)
    b = _biz(db, daily_token_budget=100)
    other = _biz(db, name="OTRO", daily_token_budget=100)
    _seed_messages(db, b, 10, 10, now)  # hoy: 20 tokens
    _seed_messages(db, b, 9000, 9000, yesterday)  # ayer: no cuenta
    _seed_messages(db, other, 5000, 5000, now)  # otro negocio: no cuenta

    assert check_daily_budget(db, b) is True

    b.daily_token_budget = 1
    db.commit()
    assert check_daily_budget(db, b) is False


def test_daily_budget_falls_back_to_settings(db, monkeypatch):
    b = _biz(db, daily_token_budget=0)
    _seed_messages(db, b, 50, 50, _utcnow())  # 100 tokens hoy

    monkeypatch.setattr(settings, "daily_token_budget", 50)
    assert check_daily_budget(db, b) is False
    monkeypatch.setattr(settings, "daily_token_budget", 1000)
    assert check_daily_budget(db, b) is True


def test_enforce_budget_raises_429_when_exceeded(db):
    b = _biz(db, daily_token_budget=1000)
    # bajo presupuesto: no lanza
    enforce_budget(db, b)
    # excedido: 429 con el detalle exacto
    _seed_messages(db, b, 600, 600, _utcnow())
    with pytest.raises(HTTPException) as exc:
        enforce_budget(db, b)
    assert exc.value.status_code == 429
    assert exc.value.detail == "daily_token_budget_exceeded"


# ---------------------------------------------------------------- RateLimitMiddleware


class _Clock:
    """Reloj congelado: ventanas deterministas, sin sleeps."""

    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


def _limited_app(session_factory, window_seconds, limit, clock):
    app = FastAPI()

    @app.post("/api/chat")
    def chat():
        return {"ok": True}

    @app.post("/api/leads")
    def leads():
        return {"ok": True}

    @app.get("/api/leads")
    def leads_get():
        return {"ok": True}

    @app.get("/api/appointments")
    def appointments_get():
        return {"ok": True}

    @app.post("/api/webhook/chatwoot")
    def webhook():
        return {"ok": True}

    @app.post("/api/otro")
    def otro():
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"ok": True}

    app.add_middleware(
        RateLimitMiddleware,
        window_seconds=window_seconds,
        limit=limit,
        session_factory=session_factory,
        clock=clock,
    )
    return app


def test_rate_limit_429_ip_isolation_and_window_rollover(db, session_factory):
    clock = _Clock(1_700_000_000.0)
    app = _limited_app(session_factory, window_seconds=0.5, limit=2, clock=clock)
    ip1 = TestClient(app, client=("10.0.0.1", 40000))

    assert ip1.post("/api/chat").status_code == 200
    assert ip1.post("/api/chat").status_code == 200
    blocked = ip1.post("/api/chat")
    assert blocked.status_code == 429
    assert blocked.json() == {"detail": "rate_limited"}

    # el límite cubre también /api/leads y el webhook...
    assert ip1.post("/api/leads").status_code == 429
    assert ip1.post("/api/webhook/chatwoot").status_code == 429
    # ...pero /health y rutas fuera de la lista quedan libres
    assert ip1.get("/health").status_code == 200
    assert ip1.post("/api/otro").status_code == 200

    # otra IP no está afectada
    ip2 = TestClient(app, client=("10.0.0.2", 40000))
    assert ip2.post("/api/chat").status_code == 200

    # pasada la ventana vuelve a permitir
    clock.t += 0.5
    assert ip1.post("/api/chat").status_code == 200

    # contadores persistidos en BD (consistentes entre workers)
    w1 = datetime.fromtimestamp(1_700_000_000.0, tz=timezone.utc).replace(tzinfo=None)
    w2 = datetime.fromtimestamp(1_700_000_000.5, tz=timezone.utc).replace(tzinfo=None)
    rows = {(r.bucket_key, r.window_start): r.hits for r in db.query(RateLimitBucket)}
    # los intentos rechazados también cuentan en la ventana
    assert rows[("ip:10.0.0.1", w1)] == 5
    assert rows[("ip:10.0.0.1", w2)] == 1
    assert rows[("ip:10.0.0.2", w1)] == 1


def test_rate_limit_429_en_lectura_del_crm(db, session_factory):
    """H19: los GET del CRM también frenan la fuerza bruta del token."""
    clock = _Clock(1_700_000_000.0)
    app = _limited_app(session_factory, window_seconds=0.5, limit=2, clock=clock)
    ip = TestClient(app, client=("10.0.0.3", 40000))

    assert ip.get("/api/leads").status_code == 200
    assert ip.get("/api/appointments").status_code == 200
    blocked = ip.get("/api/leads")
    assert blocked.status_code == 429
    assert blocked.json() == {"detail": "rate_limited"}
    assert ip.get("/api/appointments").status_code == 429

    # /health sigue libre en GET
    assert ip.get("/health").status_code == 200

    # otra IP no está afectada
    ip2 = TestClient(app, client=("10.0.0.4", 40000))
    assert ip2.get("/api/leads").status_code == 200

    # pasada la ventana vuelve a permitir
    clock.t += 0.5
    assert ip.get("/api/leads").status_code == 200


def test_install_middlewares_adds_cors():
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    install_middlewares(app)
    client = TestClient(app)

    preflight = client.options(
        "/api/chat",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers.get("access-control-allow-origin") in (
        "*",
        "http://localhost:3000",
    )
    assert client.get("/health").status_code == 200


# ---------------------------------------------------------------- observability


def test_log_turn_persists_event_log_row(db):
    b = _biz(db)
    conv = _seed_messages(db, b, 1, 1, _utcnow())
    log_turn(
        db,
        request_id="req-1",
        business_id=b.id,
        conversation_id=conv.id,
        intent="ventas",
        decision="act",
        issues=["price_without_source"],
        tools={"search_products": {"n": 1}},
        tokens_in=120,
        tokens_out=45,
        cost_est=0.003,
        latency_ms=812,
        message="turno ok",
    )
    row = db.query(EventLog).one()
    assert row.event == "turn"
    assert row.request_id == "req-1"
    assert row.business_id == b.id
    assert row.conversation_id == conv.id
    assert row.intent == "ventas"
    assert row.decision == "act"
    assert row.issues == ["price_without_source"]
    assert row.tools == {"search_products": {"n": 1}}
    assert row.tokens_in == 120
    assert row.tokens_out == 45
    assert row.cost_est == 0.003
    assert row.latency_ms == 812
    assert row.message == "turno ok"
    assert row.level == "info"


def test_log_turn_with_few_args_uses_defaults(db):
    log_turn(db, message="solo mensaje")
    row = db.query(EventLog).one()
    assert row.event == "turn"
    assert row.message == "solo mensaje"
    assert row.request_id == ""
    assert row.business_id is None
    assert row.issues == []
    assert row.tools == {}
    assert row.tokens_in == 0 and row.tokens_out == 0
    assert row.cost_est == 0.0 and row.latency_ms == 0
    assert row.level == "info"


def test_log_event_free_event_name(db):
    log_event(db, "handoff", business_id=7, message="pasado a humano", latency_ms=5)
    row = db.query(EventLog).one()
    assert row.event == "handoff"
    assert row.business_id == 7
    assert row.message == "pasado a humano"
    assert row.latency_ms == 5


def test_log_turn_never_raises_when_commit_fails():
    class BrokenSession:
        def __init__(self):
            self.added = None
            self.rolled_back = False

        def add(self, obj):
            self.added = obj

        def commit(self):
            raise RuntimeError("commit roto")

        def rollback(self):
            self.rolled_back = True

    broken = BrokenSession()
    log_turn(broken, business_id=7, message="no debe lanzar")
    assert broken.added is not None
    assert broken.rolled_back


def test_json_formatter_emits_all_fields():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    log = logging.getLogger("test_observability_json")
    log.handlers = [handler]
    log.propagate = False
    log.setLevel(logging.INFO)
    try:
        log.info(
            "turno listo",
            extra={
                "event": "turn",
                "request_id": "abc",
                "business_id": 3,
                "tokens_in": 10,
                "tokens_out": 5,
                "issues": ["x"],
                "tools": {"t": 1},
            },
        )
    finally:
        log.handlers = []
        log.propagate = True

    data = json.loads(stream.getvalue().strip())
    assert data["level"] == "info"
    assert data["event"] == "turn"
    assert data["request_id"] == "abc"
    assert data["business_id"] == 3
    assert data["tokens_in"] == 10 and data["tokens_out"] == 5
    assert data["issues"] == ["x"] and data["tools"] == {"t": 1}
    assert data["message"] == "turno listo"
    for key in ("ts", "conversation_id", "intent", "decision", "cost_est", "latency_ms"):
        assert key in data


def test_configure_logging_is_idempotent():
    def handlers():
        root = logging.getLogger()
        return [h for h in root.handlers if isinstance(h.formatter, JsonFormatter)]

    before = handlers()
    configure_logging()
    configure_logging()
    after = handlers()
    expected = len(before) + 1 if not before else len(before)
    assert len(after) == expected


def test_new_request_id_is_unique_hex():
    rid = new_request_id()
    assert re.fullmatch(r"[0-9a-f]{32}", rid)
    assert new_request_id() != rid
