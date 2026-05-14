"""Tests for the ARQ task functions (called directly, without a real worker)."""

from albumine.db import ScanStatus
from albumine.pipeline import PipelineResult
from albumine.tasks import process_pair_task, scan_input_task


class _FakePipeline:
    def __init__(self):
        self.processed: list[str] = []

    async def process_pair(self, pair, *, force=False, enhancement_level=None):
        self.processed.append(pair.pair_id)
        return PipelineResult(
            pair_id=pair.pair_id,
            status=ScanStatus.DONE,
            output_path=None,
            used_fallback=False,
        )


class _FakeRedis:
    def __init__(self):
        self.jobs: list[tuple] = []

    async def enqueue_job(self, function, *args, _job_id=None, **kwargs):
        self.jobs.append((function, args, _job_id))


async def test_process_pair_task_runs_pipeline(make_jpeg, tmp_path):
    front = make_jpeg(tmp_path / "foto_001a.jpg")
    back = make_jpeg(tmp_path / "foto_001b.jpg")
    from albumine.ingest.models import DetectionMethod, PageRef, ScanPair

    pair = ScanPair(
        pair_id="pair-9",
        front=PageRef(front),
        back=PageRef(back),
        method=DetectionMethod.IMAGE_PAIR,
        source_files=(front, back),
    )
    pipeline = _FakePipeline()

    result = await process_pair_task({"pipeline": pipeline}, pair.as_dict())

    assert result == {"pair_id": "pair-9", "status": "done"}
    assert pipeline.processed == ["pair-9"]


async def test_scan_input_task_enqueues_one_job_per_pair(
    app_settings, make_jpeg
):
    app_settings.input_dir.mkdir(parents=True, exist_ok=True)
    make_jpeg(app_settings.input_dir / "foto_001a.jpg")
    make_jpeg(app_settings.input_dir / "foto_001b.jpg")
    redis = _FakeRedis()

    count = await scan_input_task({"settings": app_settings, "redis": redis})

    assert count == 1
    assert len(redis.jobs) == 1
    function, _args, job_id = redis.jobs[0]
    assert function == "process_pair_task"
    assert job_id.startswith("pair:")
