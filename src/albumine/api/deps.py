"""Shared FastAPI dependencies and the Jinja2 templates instance.

App-wide objects (settings, DB session factory, the pipeline, the Redis pool)
are created once in :func:`albumine.main.lifespan` and stashed on
``app.state``; these helpers expose them to route handlers via ``Depends``.

The Jinja2 templates instance carries an i18n context processor, so every
rendered template has ``t`` (the translate function), ``lang`` and ``text_dir``
available without each route passing them explicitly.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

from albumine.db import ScanRecord
from albumine.db.engine import SessionFactory
from albumine.db.settings_store import get_override
from albumine.i18n import (
    LANGUAGES,
    available_languages,
    normalise_language,
    translator,
)

if TYPE_CHECKING:
    from arq import ArqRedis

    from albumine.ai.manager import ProviderManager
    from albumine.config import Settings
    from albumine.pipeline import Pipeline

_TEMPLATES_DIR = Path(__file__).parent.parent / "web" / "templates"


def resolve_language(request: Request) -> str:
    """Determine the active UI language: DB override, else the base setting."""
    base_lang = request.app.state.settings.ui_language
    session_factory = getattr(request.app.state, "session_factory", None)
    override = (
        get_override(session_factory, "ui_language") if session_factory else None
    )
    return normalise_language(override or base_lang)


def _i18n_context(request: Request) -> dict[str, object]:
    """Jinja context processor: inject the translator and language metadata."""
    lang = resolve_language(request)
    return {
        "t": translator(lang),
        "lang": lang,
        "text_dir": LANGUAGES[lang].text_direction,
        "languages": available_languages(),
    }


templates = Jinja2Templates(
    directory=str(_TEMPLATES_DIR), context_processors=[_i18n_context]
)


def get_settings(request: Request) -> Settings:
    """The application :class:`Settings` (base, env-derived)."""
    return request.app.state.settings


def get_session_factory(request: Request) -> SessionFactory:
    """A zero-arg callable that opens a new DB session."""
    return request.app.state.session_factory


def get_pipeline(request: Request) -> Pipeline:
    """The shared :class:`~albumine.pipeline.Pipeline` instance."""
    return request.app.state.pipeline


def get_provider_manager(request: Request) -> ProviderManager:
    """The provider manager (resolves the vision provider live)."""
    return request.app.state.provider_manager


def get_redis(request: Request) -> ArqRedis | None:
    """The ARQ Redis pool, or ``None`` if Redis was unreachable at startup."""
    return request.app.state.redis


def get_translator(request: Request) -> Callable[..., str]:
    """A ``t(key, **fmt)`` callable bound to the active UI language."""
    return translator(resolve_language(request))


def fetch_record(session_factory: SessionFactory, pair_id: str) -> ScanRecord:
    """Load a :class:`ScanRecord` or raise a 404."""
    with session_factory() as session:
        record = session.get(ScanRecord, pair_id)
    if record is None:
        raise HTTPException(status_code=404, detail="error.pair_not_found")
    return record
