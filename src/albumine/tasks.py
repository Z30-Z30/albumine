"""ARQ task queue: worker definition and job functions.

Long-running work (image processing, AI calls) runs here rather than in the web
request path. Two jobs:

* ``scan_input_task``  — scan the input folder and enqueue one process job per
  detected pair. Also runs on a cron as a safety net.
* ``process_pair_task`` — run a single scan pair through the pipeline.

Queue-level idempotency: process jobs are enqueued with ``_job_id`` derived from
the ``pair_id``, so a pair that is already queued or running is not duplicated.
"""

from __future__ import annotations

from typing import Any

from arq import cron
from arq.connections import RedisSettings
from arq.worker import func

from albumine.ai.manager import ProviderManager
from albumine.config import EnhancementLevel, get_settings
from albumine.db import create_db_engine, init_db, make_session_factory
from albumine.db.settings_store import effective_settings
from albumine.ingest import ScanPair, scan_directory
from albumine.logging import configure_logging, get_logger
from albumine.pipeline import Pipeline

_log = get_logger(__name__)


async def process_pair_task(
    ctx: dict[str, Any],
    pair_data: dict[str, Any],
    *,
    force: bool = False,
    enhancement_level: str | None = None,
) -> dict[str, str]:
    """Process one serialised :class:`ScanPair` through the pipeline.

    ``force=True`` re-processes a pair even if it is already ``DONE`` (used by
    the web UI's re-processing action). ``enhancement_level`` overrides the
    default image-enhancement level for this pair.
    """
    pipeline: Pipeline = ctx["pipeline"]
    pair = ScanPair.from_dict(pair_data)
    level = EnhancementLevel(enhancement_level) if enhancement_level else None
    result = await pipeline.process_pair(pair, force=force, enhancement_level=level)
    return {"pair_id": result.pair_id, "status": str(result.status)}


async def scan_input_task(ctx: dict[str, Any]) -> int:
    """Detect pairs in the input folder and enqueue a process job for each."""
    settings = ctx["settings"]
    redis = ctx["redis"]
    pairs = scan_directory(settings.input_dir)
    for pair in pairs:
        await redis.enqueue_job(
            "process_pair_task", pair.as_dict(), _job_id=f"pair:{pair.pair_id}"
        )
    _log.info("tasks.scan_enqueued", pairs=len(pairs))
    return len(pairs)


async def _on_startup(ctx: dict[str, Any]) -> None:
    base = get_settings()
    base.config_dir.mkdir(parents=True, exist_ok=True)
    engine = create_db_engine(base.database_url)
    init_db(engine)
    session_factory = make_session_factory(engine)
    # Resolve env + DB-override settings; the Pipeline re-resolves per job.
    settings = effective_settings(base, session_factory)
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    for directory in (settings.input_dir, settings.output_dir):
        directory.mkdir(parents=True, exist_ok=True)
    # The manager resolves the provider per job, so AI-setting changes from the
    # web settings panel apply to the worker without a restart.
    provider_manager = ProviderManager(base, session_factory)
    ctx["settings"] = settings
    ctx["provider_manager"] = provider_manager
    ctx["pipeline"] = Pipeline(base, provider_manager, session_factory)
    _log.info("worker.started", ai_provider=settings.ai_provider)


async def _on_shutdown(ctx: dict[str, Any]) -> None:
    provider_manager = ctx.get("provider_manager")
    if provider_manager is not None:
        await provider_manager.aclose()
    _log.info("worker.stopped")


class WorkerSettings:
    """ARQ worker configuration (referenced by ``arq albumine.tasks.WorkerSettings``)."""

    # scan_input_task keeps no result: its ``_job_id`` dedup ("scan-input") must
    # only cover queued/running scans, not block manual rescans for the whole
    # keep_result window after a scan finished.
    functions = [process_pair_task, func(scan_input_task, keep_result=0)]
    on_startup = _on_startup
    on_shutdown = _on_shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    # Safety-net rescan of the input folder every 15 minutes.
    cron_jobs = [cron(scan_input_task, minute={0, 15, 30, 45})]
    # Bound retries (no endless loops on a poison job) and allow slow ML jobs
    # (Real-ESRGAN / GFPGAN on CPU) enough time to finish.
    max_tries = 3
    job_timeout = 1200


def run_worker() -> None:
    """Console-script entrypoint: start the ARQ worker."""
    from arq import run_worker as arq_run_worker

    arq_run_worker(WorkerSettings)  # type: ignore[arg-type]
