"""Logging JSON por línea + trazabilidad por turno en EventLog (H4, spec §21)."""
import json
import logging
import sys
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import EventLog

logger = logging.getLogger("agentventas")

# Campos del evento; coinciden con las columnas de EventLog.
_DEFAULTS = {
    "request_id": "",
    "business_id": None,
    "conversation_id": None,
    "intent": "",
    "decision": "",
    "issues": [],
    "tools": {},
    "tokens_in": 0,
    "tokens_out": 0,
    "cost_est": 0.0,
    "latency_ms": 0,
    "level": "info",
    "message": "",
}


def new_request_id() -> str:
    """Id de request trazable en EventLog y en el log JSON."""
    return uuid.uuid4().hex


class JsonFormatter(logging.Formatter):
    """Una línea JSON por record con los campos del turno (H4)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "event": getattr(record, "event", "") or record.name,
        }
        for key, default in _DEFAULTS.items():
            if key in ("level", "message"):
                # level sale de record; message del msg (extra no admite "message":
                # es atributo reservado de LogRecord).
                continue
            payload[key] = getattr(record, key, default)
        payload["message"] = record.getMessage()
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging() -> None:
    """JSON por línea a stdout. Idempotente: nunca duplica el handler."""
    root = logging.getLogger()
    if any(isinstance(handler.formatter, JsonFormatter) for handler in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def log_turn(db: Session, **fields) -> None:
    """Trazabilidad por turno: fila en EventLog (event="turn") + log JSON."""
    _emit(db, "turn", fields)


def log_event(db: Session, event: str, **fields) -> None:
    """Mismo mecanismo con evento libre: handoff, error, budget_exceeded, etc."""
    _emit(db, event, fields)


def _emit(db, event: str, fields: dict) -> None:
    payload = {key: fields.get(key, default) for key, default in _DEFAULTS.items()}
    payload["event"] = str(event or "")[:40]
    payload["request_id"] = str(payload["request_id"] or "")[:36]
    payload["intent"] = str(payload["intent"] or "")[:30]
    payload["decision"] = str(payload["decision"] or "")[:30]
    payload["level"] = str(payload["level"] or "info")[:10]
    try:
        db.add(EventLog(**payload))
        db.commit()
    except Exception:
        # La observabilidad nunca rompe el request.
        try:
            db.rollback()
        except Exception:
            pass
    level = getattr(logging, payload["level"].upper(), logging.INFO)
    extra = {key: value for key, value in payload.items() if key != "message"}
    logger.log(level, payload["message"] or payload["event"], extra=extra)
