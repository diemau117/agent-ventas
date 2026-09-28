from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db.models import Base


@lru_cache
def get_engine():
    """Crea el engine de forma lazy — solo cuando se necesita.

    Esto permite que la app arranque incluso si la base de datos no está
    disponible. La conexión se establece solo cuando se necesita.
    """
    url = settings.database_url
    # pg8000: driver puro Python que no depende de OpenSSL del sistema.
    # Maneja SSL de forma diferente a psycopg2 y funciona con Render PostgreSQL.
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+pg8000://", 1)
    connect_args = {}
    if "sslmode" not in url:
        connect_args["ssl_context"] = True  # pg8000 usa ssl_context=True para SSL por defecto
    return create_engine(url, pool_pre_ping=True, connect_args=connect_args)


@lru_cache
def get_session_local():
    """Crea el sessionmaker de forma lazy."""
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)


def init_db() -> None:
    Base.metadata.create_all(get_engine())


def get_db() -> Iterator[Session]:
    db = get_session_local()()
    try:
        yield db
    finally:
        db.close()
