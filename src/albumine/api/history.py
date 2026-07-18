"""The processing-history register page."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlmodel import select

from albumine.api.deps import get_session_factory, templates
from albumine.db import ProcessingEvent
from albumine.db.engine import SessionFactory

router = APIRouter(tags=["history"])

#: Newest events shown on the register page.
PAGE_SIZE = 200


@router.get("/history", response_class=HTMLResponse)
async def history(
    request: Request,
    session_factory: Annotated[SessionFactory, Depends(get_session_factory)],
) -> HTMLResponse:
    """The register: every processing/correction event, newest first."""
    with session_factory() as session:
        events = session.exec(
            select(ProcessingEvent)
            .order_by(ProcessingEvent.created_at.desc(), ProcessingEvent.id.desc())
            .limit(PAGE_SIZE)
        ).all()
    return templates.TemplateResponse(request, "history.html", {"events": events})
