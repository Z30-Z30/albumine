"""Status dashboard: queue, errors and AI-backend health."""

from __future__ import annotations

from typing import Annotated

from arq import ArqRedis
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlmodel import func, select

from albumine.ai.manager import ProviderManager
from albumine.api.deps import (
    get_provider_manager,
    get_redis,
    get_session_factory,
    templates,
)
from albumine.db import ScanRecord, ScanStatus
from albumine.db.engine import SessionFactory
from albumine.logging import get_logger

router = APIRouter(tags=["status"])
_log = get_logger(__name__)


@router.get("/status", response_class=HTMLResponse)
async def status_dashboard(
    request: Request,
    session_factory: Annotated[SessionFactory, Depends(get_session_factory)],
    redis: Annotated[ArqRedis | None, Depends(get_redis)],
) -> HTMLResponse:
    """The status dashboard. AI health is loaded asynchronously via HTMX."""
    with session_factory() as session:
        counts = dict(
            session.exec(
                select(ScanRecord.status, func.count()).group_by(ScanRecord.status)
            ).all()
        )
        failures = session.exec(
            select(ScanRecord)
            .where(ScanRecord.status == ScanStatus.FAILED)
            .order_by(ScanRecord.updated_at.desc())
            .limit(10)
        ).all()

    status_counts = {status: counts.get(status, 0) for status in ScanStatus}

    queue_depth: int | None = None
    if redis is not None:
        try:
            queue_depth = len(await redis.queued_jobs())
        except Exception as exc:  # noqa: BLE001 - dashboard must not error out
            _log.warning("status.queue_depth_failed", error=str(exc))

    return templates.TemplateResponse(
        request,
        "status.html",
        {
            "status_counts": status_counts,
            "failures": failures,
            "queue_depth": queue_depth,
            "redis_online": redis is not None,
        },
    )


@router.get("/status/ai-health", response_class=HTMLResponse)
async def ai_health(
    request: Request,
    provider_manager: Annotated[ProviderManager, Depends(get_provider_manager)],
) -> HTMLResponse:
    """HTML fragment with the current AI-backend health (lazy-loaded)."""
    health = await provider_manager.health()
    return templates.TemplateResponse(
        request, "_ai_health.html", {"health": health}
    )
