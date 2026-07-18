"""FastAPI application entrypoint.

Wires the web layer to the rest of AlbuMine: on startup the lifespan opens the
database, builds the AI provider, connects to Redis (for the ARQ queue) and
starts the watch-folder. The actual image processing runs in the ARQ worker
(see :mod:`albumine.tasks`) — the web app only enqueues jobs and reads results.

Redis is treated as optional: if it is unreachable the app still serves the
gallery and manual corrections, only the queue-backed actions are disabled.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from importlib import metadata
from pathlib import Path

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from albumine.ai.manager import ProviderManager
from albumine.api import actions, gallery, history, settings_panel, status
from albumine.api.deps import templates
from albumine.config import Settings, get_settings
from albumine.db import create_db_engine, init_db, make_session_factory
from albumine.db.settings_store import effective_settings
from albumine.ingest import FolderWatcher
from albumine.logging import configure_logging, get_logger
from albumine.pipeline import Pipeline

_WEB_DIR = Path(__file__).parent / "web"
_log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open shared resources on startup and release them on shutdown."""
    base: Settings = app.state.settings

    # The database lives under the (env-only) config_dir; open it first, then
    # resolve the effective settings (env + DB overrides) for everything else.
    base.config_dir.mkdir(parents=True, exist_ok=True)
    engine = create_db_engine(base.database_url)
    init_db(engine)
    session_factory = make_session_factory(engine)
    settings = effective_settings(base, session_factory)

    configure_logging(level=settings.log_level, json_output=settings.log_json)
    for directory in (settings.input_dir, settings.output_dir):
        directory.mkdir(parents=True, exist_ok=True)

    # The manager builds the provider lazily and rebuilds it when AI settings
    # change, so the settings panel applies without a restart.
    provider_manager = ProviderManager(base, session_factory)

    app.state.session_factory = session_factory
    app.state.provider_manager = provider_manager
    # The Pipeline keeps the *base* settings and re-resolves overrides per job,
    # so behaviour changes from the settings panel apply without a restart.
    app.state.pipeline = Pipeline(base, provider_manager, session_factory)
    app.state.redis = await _connect_redis(settings)
    app.state.watcher = _start_watcher(settings, app.state.redis)

    _log.info(
        "albumine.startup",
        version=app.version,
        ai_provider=settings.ai_provider,
        ui_language=settings.ui_language,
        redis=app.state.redis is not None,
        watcher=app.state.watcher is not None,
    )
    yield

    if app.state.watcher is not None:
        app.state.watcher.stop()
    if app.state.redis is not None:
        await app.state.redis.close()
    await provider_manager.aclose()
    _log.info("albumine.shutdown")


async def _connect_redis(settings: Settings):
    """Connect to Redis for the ARQ queue; return ``None`` if unreachable.

    Fails fast (few retries) so a missing Redis only degrades the app, it does
    not slow startup down.
    """
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    redis_settings.conn_retries = settings.redis_connect_retries
    redis_settings.conn_retry_delay = 0.5
    try:
        pool = await create_pool(redis_settings)
        _log.info("redis.connected", url=settings.redis_url)
        return pool
    except Exception as exc:  # noqa: BLE001 - degrade gracefully without Redis
        _log.warning("redis.unavailable", url=settings.redis_url, error=str(exc))
        return None


def _start_watcher(settings: Settings, redis) -> FolderWatcher | None:
    """Start the input watch-folder; a change enqueues an input re-scan."""
    loop = asyncio.get_running_loop()

    def _on_change() -> None:
        if redis is None:
            _log.warning("watcher.change_ignored", reason="redis offline")
            return
        asyncio.run_coroutine_threadsafe(
            redis.enqueue_job("scan_input_task", _job_id="scan-input"), loop
        )

    watcher = FolderWatcher(settings.input_dir, _on_change)
    try:
        watcher.start()
        return watcher
    except Exception as exc:  # noqa: BLE001 - the app still works without the watcher
        _log.warning("watcher.start_failed", error=str(exc))
        return None


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application.

    Args:
        settings: Override settings (used by tests). Defaults to the
            environment-derived :func:`albumine.config.get_settings`.
    """
    settings = settings or get_settings()
    try:
        version = metadata.version("albumine")
    except metadata.PackageNotFoundError:  # running from source without install
        version = "0.1.0"

    app = FastAPI(title="AlbuMine", version=version, lifespan=lifespan)
    app.state.settings = settings
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict[str, str]:
        """Liveness probe used by the Docker healthcheck."""
        return {"status": "ok", "version": version}

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> HTMLResponse:
        """Render a styled HTML page for HTTP errors (404 etc.)."""
        return templates.TemplateResponse(
            request,
            "error.html",
            {"status_code": exc.status_code, "detail": exc.detail},
            status_code=exc.status_code,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> HTMLResponse:
        """Log and render a 500 page for any unhandled error."""
        _log.exception("request.unhandled_error", path=request.url.path)
        return templates.TemplateResponse(
            request,
            "error.html",
            {"status_code": 500, "detail": "error.internal"},
            status_code=500,
        )

    app.include_router(gallery.router)
    app.include_router(history.router)
    app.include_router(actions.router)
    app.include_router(status.router)
    app.include_router(settings_panel.router)
    return app


app = create_app()


def run() -> None:
    """Console-script entrypoint: serve the app with uvicorn."""
    import uvicorn

    base = get_settings()
    # Honour a host/port override from the settings panel (restart-required).
    base.config_dir.mkdir(parents=True, exist_ok=True)
    engine = create_db_engine(base.database_url)
    init_db(engine)
    settings = effective_settings(base, make_session_factory(engine))
    uvicorn.run(
        "albumine.main:app",
        host=settings.webui_host,
        port=settings.webui_port,
        log_config=None,
    )


if __name__ == "__main__":
    run()
