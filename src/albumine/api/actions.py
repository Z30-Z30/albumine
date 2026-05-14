"""Write actions: manual correction, re-processing, input re-scan.

These return HTML fragments meant to be swapped in by HTMX.
"""

from __future__ import annotations

from typing import Annotated

from arq import ArqRedis
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from albumine.api.deps import (
    fetch_record,
    get_pipeline,
    get_redis,
    get_session_factory,
    templates,
)
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
        flash = ("ok", "Korrektur gespeichert und in die Bilddatei geschrieben.")
    except ExifToolError as exc:
        _log.warning("actions.correction_write_failed", pair_id=pair_id, error=str(exc))
        flash = ("error", f"Metadaten konnten nicht geschrieben werden: {exc}")

    record = fetch_record(session_factory, pair_id)
    return templates.TemplateResponse(
        request, "_correction_form.html", {"record": record, "flash": flash}
    )


@router.post("/pair/{pair_id}/reprocess", response_class=HTMLResponse)
async def reprocess_pair(
    pair_id: str,
    session_factory: Annotated[SessionFactory, Depends(get_session_factory)],
    redis: Annotated[ArqRedis | None, Depends(get_redis)],
) -> HTMLResponse:
    """Enqueue a forced re-processing of one pair."""
    record = fetch_record(session_factory, pair_id)
    if redis is None:
        return _flash("error", "Queue nicht verfügbar — Redis ist offline.")
    pair = pair_from_record(record)
    await redis.enqueue_job(
        "process_pair_task", pair.as_dict(), force=True, _job_id=f"pair:{pair_id}"
    )
    _log.info("actions.reprocess_enqueued", pair_id=pair_id)
    return _flash("ok", "Re-Processing wurde eingereiht.")


@router.post("/rescan", response_class=HTMLResponse)
async def rescan_input(
    redis: Annotated[ArqRedis | None, Depends(get_redis)],
) -> HTMLResponse:
    """Trigger a re-scan of the input folder."""
    if redis is None:
        return _flash("error", "Queue nicht verfügbar — Redis ist offline.")
    await redis.enqueue_job("scan_input_task", _job_id="scan-input")
    _log.info("actions.rescan_enqueued")
    return _flash("ok", "Input-Ordner wird neu eingelesen.")


def _flash(kind: str, message: str) -> HTMLResponse:
    return HTMLResponse(f'<span class="flash flash-{kind}">{message}</span>')
