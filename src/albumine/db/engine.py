"""Database engine and session helpers.

SQLite via SQLModel. Schema is created with ``create_all`` — pragmatic for a
single-file SQLite database in a Selfhost setup; a migration tool can be added
later if the schema starts changing in incompatible ways.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

# Importing the models module registers the tables on SQLModel.metadata.
from albumine.db import models as _models  # noqa: F401
from albumine.logging import get_logger

_log = get_logger(__name__)

SessionFactory = Callable[[], Session]


def create_db_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy engine for the given SQLite URL."""
    return create_engine(
        database_url,
        # FastAPI / ARQ touch the connection from different threads.
        connect_args={"check_same_thread": False},
    )


def init_db(engine: Engine) -> None:
    """Create all tables that do not exist yet."""
    SQLModel.metadata.create_all(engine)
    _log.info("db.initialised", url=str(engine.url))


def make_session_factory(engine: Engine) -> SessionFactory:
    """Return a zero-arg callable that opens a new :class:`Session`."""
    return lambda: Session(engine)
