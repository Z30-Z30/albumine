"""Write actions: manual correction, re-processing, input re-scan.

These return HTML fragments meant to be swapped in by HTMX.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from typing import Annotated

from arq import ArqRedis
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from albumine.api.deps import (
    fetch_record,
    get_pipeline,
    get_redis,
    get_session_factory,
    get_translator,
    templates,
)
from albumine.config import EnhancementLevel
from albumine.db.engine import SessionFactory
from albumine.logging import get_logger
from albumine.pipeline import Pipeline, pair_from_record
from albumine.processing.metadata_writer import ExifToolError

router = APIRouter(tags=["actions"])
_log = get_logger(__name__)


@router.post("/pair/{pair_id}/correct", response_class=HTMLResponse)
async def correct_pair(
    request: Request,
    pair_id: str,
    pipeline: Annotated[Pipeline, Depends(get_pipeline)],
    session_factory: Annotated[SessionFactory, Depends(get_session_factory)],
    t: Annotated[Callable[..., str], Depends(get_translator)],
    raw_text: Annotated[str, Form()] = "",
    date_text: Annotated[str, Form()] = "",
    location: Annotated[str, Form()] = "",
    people: Annotated[str, Form()] = "",
    event: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Apply manual corrections and re-write the image metadata."""
    people_list = [name.strip() for name in people.split(",") if name.strip()]
    try:
        pipeline.apply_manual_correction(
            pair_id,
            raw_text=raw_text,
            date_text=date_text,
            location=location,
            people=people_list,
            event=event,
            notes=notes,
        )
        flash = ("ok", t("flash.correction_saved"))
    except ExifToolError as exc:
        _log.warning("actions.correction_write_failed", pair_id=pair_id, error=str(exc))
        flash = ("error", t("flash.correction_failed", error=str(exc)))

    record = fetch_record(session_factory, pair_id)
    return templates.TemplateResponse(
        request, "_correction_form.html", {"record": record, "flash": flash}
    )


@router.post("/pair/{pair_id}/reprocess", response_class=HTMLResponse)
async def reprocess_pair(
    pair_id: str,
    session_factory: Annotated[SessionFactory, Depends(get_session_factory)],
    redis: Annotated[ArqRedis | None, Depends(get_redis)],
    t: Annotated[Callable[..., str], Depends(get_translator)],
    enhancement_level: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Enqueue a forced re-processing of one pair, optionally at a given level."""
    record = fetch_record(session_factory, pair_id)
    if redis is None:
        return _flash("error", t("flash.queue_unavailable"))

    try:
        level = EnhancementLevel(enhancement_level) if enhancement_level else None
    except ValueError:
        return _flash("error", t("flash.unknown_level", value=enhancement_level))

    pair = pair_from_record(record)
    await redis.enqueue_job(
        "process_pair_task",
        pair.as_dict(),
        force=True,
        enhancement_level=str(level) if level else None,
        _job_id=f"pair:{pair_id}",
    )
    _log.info(
        "actions.reprocess_enqueued",
        pair_id=pair_id,
        level=str(level) if level else "default",
    )
    return _flash("ok", t("flash.reprocess_queued"))


@router.post("/rescan", response_class=HTMLResponse)
async def rescan_input(
    redis: Annotated[ArqRedis | None, Depends(get_redis)],
    t: Annotated[Callable[..., str], Depends(get_translator)],
) -> HTMLResponse:
    """Trigger a re-scan of the input folder."""
    if redis is None:
        return _flash("error", t("flash.queue_unavailable"))
    # The fixed job id guards against parallel scans: ARQ returns None instead
    # of enqueueing when a scan is already queued or running.
    job = await redis.enqueue_job("scan_input_task", _job_id="scan-input")
    if job is None:
        _log.info("actions.rescan_already_queued")
        return _flash("warn", t("flash.rescan_already_running"))
    _log.info("actions.rescan_enqueued")
    return _flash("ok", t("flash.rescan_started"))


def _flash(kind: str, message: str) -> HTMLResponse:
    return HTMLResponse(f'<span class="flash flash-{kind}">{html.escape(message)}</span>')
