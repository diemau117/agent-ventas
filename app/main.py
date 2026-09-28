"""Entrypoint: lifespan con fail-fast de producción + routers + frontend."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import chat, health
from app.config import settings
from app.db.database import init_db

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail-fast: en prod no se arranca sin GROQ_API_KEY ni con DB local.
    settings.validate_runtime()
    # init_db() es opcional: si la BD no está disponible, la app arranca
    # de todas formas y maneja el error cuando se necesite la BD.
    try:
        init_db()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"init_db() falló (app arranca sin BD): {e}")
    yield


app = FastAPI(title="Agent Ventas API", lifespan=lifespan)

# Logging y middlewares (CORS + rate limit) ANTES de que arranque la app:
# Starlette prohíbe add_middleware una vez iniciada, por eso va a nivel de
# módulo y no dentro del lifespan. Import tolerante para desarrollo incremental.
try:
    from app.observability import configure_logging

    configure_logging()
except ImportError:  # pragma: no cover
    pass
try:
    from app.middleware import install_middlewares

    install_middlewares(app)
except ImportError:  # pragma: no cover
    pass

# Routers de la landing (catálogo, captación de leads) y del webhook de
# Chatwoot. Prefijo /api; tolerantes a import faltante.
for _mod in (
    "app.api.routes.catalog",
    "app.api.routes.leads",
    "app.api.routes.webhook",
    "app.api.routes.crm",
    "app.api.routes.onboarding",
    "app.api.routes.panel",
    "app.api.routes.control_center",
    "app.api.routes.whatsapp",
    "app.api.routes.stripe",
):
    try:
        _r = __import__(_mod, fromlist=["router"]).router
        app.include_router(_r, prefix="/api")
    except ImportError:  # pragma: no cover
        pass

app.include_router(chat.router, prefix="/api")
app.include_router(health.router)


@app.get("/")
async def index():
    """Sirve la landing inyectando la clave pública del tenant.

    El HTML trae `window.AGENT_VENTAS_KEY = ""` como punto único de
    configuración; acá se reemplaza por `settings.landing_public_key` para
    que el widget sepa a qué negocio consultar sin exponer business_id.
    """
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    key = settings.landing_public_key
    if key:
        html = html.replace('window.AGENT_VENTAS_KEY = ""', f'window.AGENT_VENTAS_KEY = "{key}"')
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/sales")
async def sales():
    """Landing de ventas para atraer clientes."""
    html = (FRONTEND / "sales.html").read_text(encoding="utf-8")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/onboarding-page")
async def onboarding():
    """Página de onboarding para nuevos clientes."""
    html = (FRONTEND / "onboarding.html").read_text(encoding="utf-8")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/panel")
async def panel():
    """Panel de control del cliente (requiere token en query param)."""
    html = (FRONTEND / "panel.html").read_text(encoding="utf-8")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


# Sirve los assets de la landing/widget sin exponer el resto del repo.
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
