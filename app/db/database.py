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
    connect_args = {}
    # psycopg2 + Render PostgreSQL: sslmode como connect_arg es más confiable
    # que en la URL (evita "SSL connection has been closed unexpectedly")
    if "sslmode" not in url:
        connect_args["sslmode"] = "verify-ca"
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
