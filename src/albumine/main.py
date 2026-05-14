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
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from albumine.ai import build_provider
from albumine.api import actions, gallery, status
from albumine.config import Settings, get_settings
from albumine.db import create_db_engine, init_db, make_session_factory
from albumine.ingest import FolderWatcher
from albumine.logging import configure_logging, get_logger
from albumine.pipeline import Pipeline

_WEB_DIR = Path(__file__).parent / "web"
_log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open shared resources on startup and release them on shutdown."""
    settings: Settings = app.state.settings
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    for directory in (settings.input_dir, settings.output_dir, settings.config_dir):
        directory.mkdir(parents=True, exist_ok=True)

    engine = create_db_engine(settings.database_url)
    init_db(engine)
    session_factory = make_session_factory(engine)
    provider = build_provider(settings)

    app.state.session_factory = session_factory
    app.state.provider = provider
    app.state.pipeline = Pipeline(settings, provider, session_factory)
    app.state.redis = await _connect_redis(settings)
    app.state.watcher = _start_watcher(settings, app.state.redis)

    _log.info(
        "albumine.startup",
        version=app.version,
        ai_provider=settings.ai_provider,
        redis=app.state.redis is not None,
        watcher=app.state.watcher is not None,
    )
    yield

    if app.state.watcher is not None:
        app.state.watcher.stop()
    if app.state.redis is not None:
        await app.state.redis.close()
    await provider.aclose()
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

    app.include_router(gallery.router)
    app.include_router(actions.router)
    app.include_router(status.router)
    return app


app = create_app()


def run() -> None:
    """Console-script entrypoint: serve the app with uvicorn."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "albumine.main:app",
        host=settings.webui_host,
        port=settings.webui_port,
        log_config=None,
    )


if __name__ == "__main__":
    run()
