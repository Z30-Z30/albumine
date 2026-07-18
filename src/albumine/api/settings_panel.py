"""The web settings panel — view and edit runtime configuration overrides.

Reads the effective configuration (env + DB overrides), renders an editable
form grouped by category, and writes changes back as
:class:`~albumine.db.models.AppSetting` rows. Secrets are never echoed back to
the browser — an empty secret field on submit means "keep the current value".
"""

from __future__ import annotations

import asyncio
import html
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated
from xmlrpc.client import ServerProxy

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse

from albumine.api.deps import get_session_factory, get_translator, templates
from albumine.config import Settings
from albumine.db.engine import SessionFactory
from albumine.db.settings_store import (
    CATEGORIES,
    EDITABLE_SETTINGS,
    effective_settings,
    load_overrides,
    save_overrides,
    validate_overrides,
)
from albumine.logging import get_logger

router = APIRouter(tags=["settings"])
_log = get_logger(__name__)


def _field_views(effective: Settings) -> list[dict[str, object]]:
    """Build the per-field view model for the settings template."""
    views: list[dict[str, object]] = []
    for spec in EDITABLE_SETTINGS:
        value = getattr(effective, spec.key)
        is_secret = spec.kind == "secret"
        views.append(
            {
                "key": spec.key,
                "category": spec.category,
                "kind": spec.kind,
                "choices": spec.choices,
                "restart_required": spec.restart_required,
                "value": "" if (is_secret or value is None) else str(value),
                "secret_set": is_secret and bool(value),
                "description": Settings.model_fields[spec.key].description or "",
            }
        )
    return views


def _render(
    request: Request,
    effective: Settings,
    flash: tuple[str, str] | None = None,
) -> HTMLResponse:
    base: Settings = request.app.state.settings
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "fields": _field_views(effective),
            "categories": CATEGORIES,
            "flash": flash,
            "restart_available": bool(base.supervisor_url),
        },
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    session_factory: Annotated[SessionFactory, Depends(get_session_factory)],
) -> HTMLResponse:
    """Render the settings panel with current effective values."""
    base: Settings = request.app.state.settings
    return _render(request, effective_settings(base, session_factory))


@router.post("/settings", response_class=HTMLResponse)
async def save_settings(
    request: Request,
    session_factory: Annotated[SessionFactory, Depends(get_session_factory)],
    t: Annotated[Callable[..., str], Depends(get_translator)],
) -> HTMLResponse:
    """Validate and persist submitted settings overrides."""
    base: Settings = request.app.state.settings
    form = await request.form()

    updates: dict[str, str] = {}
    for spec in EDITABLE_SETTINGS:
        if spec.kind == "bool":
            updates[spec.key] = "true" if form.get(spec.key) else "false"
        elif spec.kind == "secret":
            submitted = str(form.get(spec.key) or "").strip()
            if submitted:  # empty secret field => keep the stored value
                updates[spec.key] = submitted
        else:
            updates[spec.key] = str(form.get(spec.key) or "").strip()

    # Validate the full prospective override set before writing anything.
    prospective = {**load_overrides(session_factory), **updates}
    error = validate_overrides(base, prospective)
    if error is not None:
        _log.warning("settings.save_rejected", error=error)
        effective = effective_settings(base, session_factory)
        return _render(request, effective, flash=("error", t("settings.error", error=error)))

    save_overrides(session_factory, updates)
    effective = effective_settings(base, session_factory)
    return _render(request, effective, flash=("ok", t("settings.saved")))


# --- in-app server restart ---------------------------------------------------

#: Grace period so the HTTP response reaches the browser before the restart.
_RESTART_DELAY_SECONDS = 0.5

#: Swapped in by HTMX together with the success flash: waits for the server to
#: go down and come back up, then reloads the page (fallback after ~2 minutes).
_RELOAD_SCRIPT = """
<script>
(function () {
    var wentDown = false;
    var tries = 0;
    function poll() {
        if (++tries > 120) { location.reload(); return; }
        fetch("/healthz", {cache: "no-store"}).then(function (response) {
            if (!response.ok) { wentDown = true; }
            else if (wentDown) { location.reload(); return; }
            setTimeout(poll, 1000);
        }).catch(function () { wentDown = true; setTimeout(poll, 1000); });
    }
    setTimeout(poll, 1000);
})();
</script>
"""


def _supervisor(url: str) -> ServerProxy:
    """XML-RPC handle to supervisord (module-level so tests can stub it)."""
    return ServerProxy(url)


def _restart_supervisor(url: str) -> None:
    """Restart supervisord (and with it Redis, worker and web). Runs as a
    background task after the HTTP response has been sent."""
    time.sleep(_RESTART_DELAY_SECONDS)
    try:
        _supervisor(url).supervisor.restart()
    except Exception as exc:  # noqa: BLE001 - nothing left to report to but the log
        _log.error("settings.restart_failed", error=str(exc))


def _restart_flash(kind: str, message: str, *, reload_script: bool = False) -> HTMLResponse:
    fragment = f'<span class="flash flash-{kind}">{html.escape(message)}</span>'
    if reload_script:
        fragment += _RELOAD_SCRIPT
    return HTMLResponse(fragment)


@router.post("/settings/restart", response_class=HTMLResponse)
async def restart_server(
    request: Request,
    background_tasks: BackgroundTasks,
    t: Annotated[Callable[..., str], Depends(get_translator)],
) -> HTMLResponse:
    """Restart the whole application via supervisord's XML-RPC interface."""
    base: Settings = request.app.state.settings
    url = base.supervisor_url
    if not url:
        return _restart_flash("error", t("flash.restart_unavailable"))

    # Probe first so a missing supervisord (e.g. compose per-service mode)
    # yields an honest error instead of a restart that never happens.
    try:
        await asyncio.to_thread(lambda: _supervisor(url).supervisor.getState())
    except Exception as exc:  # noqa: BLE001 - any RPC failure means "cannot restart"
        _log.warning("settings.restart_probe_failed", error=str(exc))
        return _restart_flash("error", t("flash.restart_failed", error=str(exc)))

    background_tasks.add_task(_restart_supervisor, url)
    _log.info("settings.restart_triggered")
    return _restart_flash("ok", t("flash.restart_triggered"), reload_script=True)


# --- Ollama model pull -------------------------------------------------------


@dataclass
class OllamaPullState:
    """Progress of one ``ollama pull``, polled by the settings page."""

    model: str
    status: str = ""
    error: str | None = None
    done: bool = False
    #: Per-layer download progress, keyed by digest: (completed, total) bytes.
    layers: dict[str, tuple[int, int]] = field(default_factory=dict)

    @property
    def running(self) -> bool:
        return not self.done and self.error is None

    @property
    def percent(self) -> int | None:
        total = sum(total for _, total in self.layers.values())
        if not total:
            return None
        completed = sum(completed for completed, _ in self.layers.values())
        return min(100, int(100 * completed / total))


async def _run_ollama_pull(host: str, state: OllamaPullState) -> None:
    """Stream ``POST /api/pull`` from Ollama, mirroring progress into ``state``.

    Module-level so tests can stub it. Never raises — every failure ends up on
    ``state.error`` where the status bar shows it.
    """
    try:
        timeout = httpx.Timeout(30.0, read=None)  # model layers can take long
        async with (
            httpx.AsyncClient(timeout=timeout) as client,
            client.stream(
                "POST",
                f"{host.rstrip('/')}/api/pull",
                json={"model": state.model, "stream": True},
            ) as response,
        ):
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                data = json.loads(line)
                if error := data.get("error"):
                    state.error = error
                    _log.warning("ollama.pull_failed", model=state.model, error=error)
                    return
                state.status = data.get("status", "")
                digest = data.get("digest")
                if digest and data.get("total"):
                    state.layers[digest] = (data.get("completed", 0), data["total"])
                if state.status == "success":
                    state.done = True
        if not state.done and state.error is None:
            state.error = "pull stream ended without success"
    except Exception as exc:  # noqa: BLE001 - report any failure on the status bar
        state.error = str(exc)
        _log.warning("ollama.pull_failed", model=state.model, error=str(exc))
    else:
        if state.done:
            _log.info("ollama.pull_done", model=state.model)


def _render_pull_status(
    request: Request, *, already_running: bool = False
) -> HTMLResponse:
    state: OllamaPullState | None = getattr(request.app.state, "ollama_pull", None)
    return templates.TemplateResponse(
        request,
        "_ollama_pull.html",
        {"state": state, "already_running": already_running},
    )


@router.post("/settings/ollama/pull", response_class=HTMLResponse)
async def ollama_pull(
    request: Request,
    session_factory: Annotated[SessionFactory, Depends(get_session_factory)],
    t: Annotated[Callable[..., str], Depends(get_translator)],
) -> HTMLResponse:
    """Start pulling the entered vision model on the configured Ollama host."""
    form = await request.form()
    model = str(form.get("ollama_vision_model") or "").strip()
    if not model:
        return HTMLResponse(
            f'<span class="flash flash-error">{html.escape(t("pull.empty_model"))}</span>'
        )

    state: OllamaPullState | None = getattr(request.app.state, "ollama_pull", None)
    if state is not None and state.running:
        return _render_pull_status(request, already_running=True)

    base: Settings = request.app.state.settings
    settings = effective_settings(base, session_factory)
    state = OllamaPullState(model=model, status="starting")
    request.app.state.ollama_pull = state
    asyncio.create_task(_run_ollama_pull(settings.ollama_host, state))
    _log.info("ollama.pull_started", model=model, host=settings.ollama_host)
    return _render_pull_status(request)


@router.get("/settings/ollama/pull-status", response_class=HTMLResponse)
async def ollama_pull_status(request: Request) -> HTMLResponse:
    """Fragment with the current pull progress (polled by the settings page)."""
    return _render_pull_status(request)
