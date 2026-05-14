"""Shared FastAPI dependencies and the Jinja2 templates instance.

App-wide objects (settings, DB session factory, the pipeline, the Redis pool)
are created once in :func:`albumine.main.lifespan` and stashed on
``app.state``; these helpers expose them to route handlers via ``Depends``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

from albumine.db import ScanRecord
from albumine.db.engine import SessionFactory

if TYPE_CHECKING:
    from arq import ArqRedis

    from albumine.ai.base import VisionProvider
    from albumine.config import Settings
    from albumine.pipeline import Pipeline

_TEMPLATES_DIR = Path(__file__).parent.parent / "web" / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def get_settings(request: Request) -> Settings:
    """The application :class:`Settings`."""
    return request.app.state.settings


def get_session_factory(request: Request) -> SessionFactory:
    """A zero-arg callable that opens a new DB session."""
    return request.app.state.session_factory


def get_pipeline(request: Request) -> Pipeline:
    """The shared :class:`~albumine.pipeline.Pipeline` instance."""
    return request.app.state.pipeline


def get_provider(request: Request) -> VisionProvider:
    """The configured vision provider (used for health checks)."""
    return request.app.state.provider


def get_redis(request: Request) -> ArqRedis | None:
    """The ARQ Redis pool, or ``None`` if Redis was unreachable at startup."""
    return request.app.state.redis


def fetch_record(session_factory: SessionFactory, pair_id: str) -> ScanRecord:
    """Load a :class:`ScanRecord` or raise a 404."""
    with session_factory() as session:
        record = session.get(ScanRecord, pair_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Paar nicht gefunden")
    return record
