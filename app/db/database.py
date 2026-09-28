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
    return create_engine(settings.database_url, pool_pre_ping=True)


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
