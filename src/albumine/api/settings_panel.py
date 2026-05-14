"""The web settings panel — view and edit runtime configuration overrides.

Reads the effective configuration (env + DB overrides), renders an editable
form grouped by category, and writes changes back as
:class:`~albumine.db.models.AppSetting` rows. Secrets are never echoed back to
the browser — an empty secret field on submit means "keep the current value".
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Request
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
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "fields": _field_views(effective),
            "categories": CATEGORIES,
            "flash": flash,
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
