"""Rate limit persistente por IP (correcto multi-worker) + CORS (H16, spec §16)."""
import time
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import RateLimitBucket

# Solo endpoints que ejecutan LLM/costos: /health y /ready quedan libres.
LIMITED_PATHS = frozenset({"/api/chat", "/api/leads", "/api/webhook/chatwoot"})
# H19: la lectura del CRM también limita (GET) para frenar la fuerza bruta
# del token contra /api/leads y /api/appointments.
CRM_GET_PATHS = frozenset({"/api/leads", "/api/appointments"})


def _client_ip(request: Request) -> str:
    """IP real del visitante para el rate limit.

    Detrás de cloudflared el peer del socket es 127.0.0.1 para todos los
    visitantes, así que sin confiar en los headers del proxy todo el mundo
    compartiría UN bucket de 30/min y el límite saltaría enseguida.

    Solo se hace con TRUST_PROXY_HEADERS=true: fuera de Cloudflare cualquiera
    puede mandar esos headers y anularía el límite.
    """
    if settings.trust_proxy_headers:
        # X-Real-IP: lo pone únicamente el Worker de la landing. En el
        # segundo salto (Worker -> tunnel) Cloudflare sobrescribe
        # X-Forwarded-For con su propia IP, así que sin este header el
        # backend terminaría midiendo al Worker y todos los visitantes
        # compartirían UN bucket de 30/min.
        real = request.headers.get("x-real-ip")
        if real:
            return real.strip()
        cf = request.headers.get("cf-connecting-ip")
        if cf:
            return cf.strip()
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Ventana fija por IP con contador en RateLimitBucket.

    El upsert contra la BD (y no un dict en memoria) hace que el conteo sea
    consistente entre múltiples workers de uvicorn.
    """

    def __init__(
        self,
        app: ASGIApp,
        window_seconds: float = 60.0,
        limit: int | None = None,
        session_factory=None,
        clock=time.time,
    ):
        super().__init__(app)
        self.window_seconds = float(window_seconds)
        self.limit = settings.rate_limit_per_min if limit is None else int(limit)
        self.session_factory = session_factory or SessionLocal
        self.clock = clock

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        limited = (request.method == "POST" and path in LIMITED_PATHS) or (
            request.method == "GET" and path in CRM_GET_PATHS
        )
        if not limited:
            return await call_next(request)
        if not self._allow(_client_ip(request)):
            return JSONResponse(status_code=429, content={"detail": "rate_limited"})
        return await call_next(request)

    def _window_start(self) -> datetime:
        # Con window_seconds=60 esto es el minuto UTC actual (ventana fija).
        slot = int(self.clock() // self.window_seconds) * self.window_seconds
        return datetime.fromtimestamp(slot, tz=timezone.utc).replace(tzinfo=None)

    def _allow(self, ip: str) -> bool:
        window_start = self._window_start()
        db: Session = self.session_factory()
        try:
            table = RateLimitBucket.__table__
            dialect = db.get_bind().dialect.name
            make_insert = postgres_insert if dialect == "postgresql" else sqlite_insert
            stmt = (
                make_insert(table)
                .values(bucket_key=f"ip:{ip}", window_start=window_start, hits=1)
                .on_conflict_do_update(
                    index_elements=["bucket_key", "window_start"],
                    set_={"hits": table.c.hits + 1},
                )
                .returning(table.c.hits)
            )
            hits = db.execute(stmt).scalar_one()
            db.commit()
            return hits <= self.limit
        except Exception:
            # Si la BD no responde el request sigue (fail-open): el freno real
            # de la cuota del LLM es el presupuesto diario, no este contador.
            try:
                db.rollback()
            except Exception:
                pass
            return True
        finally:
            db.close()


def install_middlewares(app: FastAPI) -> None:
    """CORS + rate limit persistente. Lo llama el orquestador desde main.py."""
    origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        allow_credentials=False,
    )
    app.add_middleware(RateLimitMiddleware)
