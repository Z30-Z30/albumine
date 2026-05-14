"""FastAPI application entrypoint.

Phase 1: a runnable app with a health endpoint and a placeholder landing page.
Ingest, processing, AI and the full web UI are wired up in later phases.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from importlib import metadata
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from albumine.config import get_settings
from albumine.logging import configure_logging, get_logger

_WEB_DIR = Path(__file__).parent / "web"
_log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Configure logging on startup; ensure volume directories exist."""
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    for directory in (settings.input_dir, settings.output_dir, settings.config_dir):
        directory.mkdir(parents=True, exist_ok=True)
    _log.info(
        "albumine.startup",
        version=app.version,
        ai_provider=settings.ai_provider,
        webui_port=settings.webui_port,
    )
    yield
    _log.info("albumine.shutdown")


def create_app() -> FastAPI:
    """Build and return the FastAPI application instance."""
    try:
        version = metadata.version("albumine")
    except metadata.PackageNotFoundError:  # running from source without install
        version = "0.1.0"

    app = FastAPI(title="AlbuMine", version=version, lifespan=lifespan)

    templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict[str, str]:
        """Liveness probe used by the Docker healthcheck."""
        return {"status": "ok", "version": version}

    @app.get("/", response_class=HTMLResponse, tags=["web"])
    async def index(request: Request) -> HTMLResponse:
        """Placeholder landing page — the real gallery UI arrives in Phase 6."""
        return templates.TemplateResponse(request, "index.html", {"version": version})

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
