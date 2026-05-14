"""Gallery and pair-detail pages, plus image serving."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from sqlmodel import select

from albumine.api.deps import fetch_record, get_session_factory, templates
from albumine.db import ScanRecord
from albumine.db.engine import SessionFactory
from albumine.ingest.models import PageRef
from albumine.logging import get_logger
from albumine.processing.front import FrontProcessingError, load_source

router = APIRouter(tags=["web"])
_log = get_logger(__name__)


@router.get("/", response_class=HTMLResponse)
async def gallery(
    request: Request,
    session_factory: Annotated[SessionFactory, Depends(get_session_factory)],
) -> HTMLResponse:
    """The gallery: every detected scan pair, newest first."""
    with session_factory() as session:
        records = session.exec(
            select(ScanRecord).order_by(ScanRecord.updated_at.desc())
        ).all()
    return templates.TemplateResponse(
        request, "gallery.html", {"records": records}
    )


@router.get("/pair/{pair_id}", response_class=HTMLResponse)
async def pair_detail(
    request: Request,
    pair_id: str,
    session_factory: Annotated[SessionFactory, Depends(get_session_factory)],
) -> HTMLResponse:
    """Detail view of one pair: front + back images and the correction form."""
    record = fetch_record(session_factory, pair_id)
    return templates.TemplateResponse(
        request, "detail.html", {"record": record, "flash": None}
    )


@router.get("/pair/{pair_id}/image/{side}")
async def pair_image(
    pair_id: str,
    side: str,
    session_factory: Annotated[SessionFactory, Depends(get_session_factory)],
) -> Response:
    """Serve the front (processed output) or back (rasterised source) image."""
    if side not in {"front", "back"}:
        raise HTTPException(status_code=404, detail="unbekannte Seite")
    record = fetch_record(session_factory, pair_id)

    if side == "front":
        if not record.output_path or not Path(record.output_path).is_file():
            raise HTTPException(status_code=404, detail="kein verarbeitetes Bild")
        return FileResponse(record.output_path, media_type="image/jpeg")

    if not record.back_path:
        raise HTTPException(status_code=404, detail="dieses Paar hat keine Rückseite")
    page_ref = PageRef(Path(record.back_path), record.back_page_index)
    try:
        image = load_source(page_ref).convert("RGB")
    except FrontProcessingError as exc:
        _log.warning("gallery.back_image_failed", pair_id=pair_id, error=str(exc))
        raise HTTPException(status_code=404, detail="Rückseite nicht lesbar") from exc
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=85)
    return Response(content=buffer.getvalue(), media_type="image/jpeg")
