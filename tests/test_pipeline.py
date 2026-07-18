"""Tests for the end-to-end processing pipeline."""

from types import SimpleNamespace

from albumine.ai.base import BackExtraction, ExtractedDate
from albumine.config import EnhancementLevel
from albumine.db import ScanRecord, ScanStatus
from albumine.ingest.models import DetectionMethod, PageRef, ScanPair
from albumine.parsing.date_parser import Confidence, DatePrecision
from albumine.pipeline import Pipeline, reconcile_date
from albumine.processing import back as back_module


class _RecordingWriter:
    """A metadata_writer stand-in that records calls instead of running ExifTool."""

    def __init__(self):
        self.calls: list[SimpleNamespace] = []

    def __call__(self, path, metadata, sidecar):
        self.calls.append(SimpleNamespace(path=path, metadata=metadata, sidecar=sidecar))


def _make_pair(make_jpeg, input_dir, *, with_back=True, needs_review=False, pair_id="pair-1"):
    front = make_jpeg(input_dir / "foto_001a.jpg")
    back = make_jpeg(input_dir / "foto_001b.jpg") if with_back else None
    return ScanPair(
        pair_id=pair_id,
        front=PageRef(front),
        back=PageRef(back) if back else None,
        method=DetectionMethod.IMAGE_PAIR,
        source_files=(front, back) if back else (front,),
        needs_review=needs_review,
    )


_RICH_EXTRACTION = BackExtraction(
    raw_text="Hochzeit Anna & Hans, Zürich, Mai 1973",
    date=ExtractedDate(iso="1973-05", original_text="Mai 1973", confidence="high"),
    location="Zürich",
    people=["Anna", "Hans"],
    event="Hochzeit",
)


# --- reconcile_date ---------------------------------------------------------


def test_reconcile_date_prefers_deterministic_parser():
    result = reconcile_date(
        ExtractedDate(iso="1973", original_text="Mai 1973", confidence=Confidence.HIGH)
    )
    assert result.iso == "1973-05"  # parser read the month, not just the year
    assert result.precision is DatePrecision.MONTH
    assert result.confidence is Confidence.HIGH


def test_reconcile_date_uses_weakest_confidence():
    result = reconcile_date(
        ExtractedDate(iso="1973-05", original_text="Mai 1973", confidence=Confidence.LOW)
    )
    # Parser is confident, but the model said it could barely read it.
    assert result.confidence is Confidence.LOW


def test_reconcile_date_falls_back_to_model_iso():
    result = reconcile_date(
        ExtractedDate(iso="1980", original_text="unleserlich", confidence=Confidence.MEDIUM)
    )
    assert result.iso == "1980"
    assert result.confidence is Confidence.MEDIUM


def test_reconcile_date_empty():
    result = reconcile_date(ExtractedDate())
    assert result.precision is DatePrecision.NONE
    assert result.datetime_original is None


# --- process_pair -----------------------------------------------------------


async def test_process_pair_success(app_settings, session_factory, fake_provider, make_jpeg):
    app_settings.input_dir.mkdir(parents=True, exist_ok=True)
    pair = _make_pair(make_jpeg, app_settings.input_dir)
    provider = fake_provider(_RICH_EXTRACTION)
    writer = _RecordingWriter()
    pipeline = Pipeline(app_settings, provider, session_factory, metadata_writer=writer)

    result = await pipeline.process_pair(pair)

    assert result.status is ScanStatus.DONE
    assert result.output_path is not None and result.output_path.exists()
    assert len(writer.calls) == 1

    with session_factory() as session:
        record = session.get(ScanRecord, "pair-1")
    assert record.status is ScanStatus.DONE
    assert record.date_iso == "1973-05"
    assert record.location == "Zürich"
    assert record.people == ["Anna", "Hans"]
    assert record.ai_provider == "fake"
    assert record.ai_model == "fake-vision-1"


async def test_process_pair_is_idempotent(app_settings, session_factory, fake_provider, make_jpeg):
    app_settings.input_dir.mkdir(parents=True, exist_ok=True)
    pair = _make_pair(make_jpeg, app_settings.input_dir)
    provider = fake_provider(_RICH_EXTRACTION)
    pipeline = Pipeline(app_settings, provider, session_factory, metadata_writer=_RecordingWriter())

    await pipeline.process_pair(pair)
    assert provider.calls == 1

    # Second run: already DONE -> skipped, provider not called again.
    second = await pipeline.process_pair(pair)
    assert second.status is ScanStatus.DONE
    assert provider.calls == 1

    # force=True re-runs it.
    await pipeline.process_pair(pair, force=True)
    assert provider.calls == 2


async def test_process_pair_without_back(app_settings, session_factory, fake_provider, make_jpeg):
    app_settings.input_dir.mkdir(parents=True, exist_ok=True)
    pair = _make_pair(make_jpeg, app_settings.input_dir, with_back=False)
    provider = fake_provider(_RICH_EXTRACTION)
    pipeline = Pipeline(app_settings, provider, session_factory, metadata_writer=_RecordingWriter())

    result = await pipeline.process_pair(pair)

    assert result.status is ScanStatus.DONE
    assert provider.calls == 0  # no back -> no AI call


async def test_needs_review_pair_stays_needs_review(
    app_settings, session_factory, fake_provider, make_jpeg
):
    app_settings.input_dir.mkdir(parents=True, exist_ok=True)
    pair = _make_pair(make_jpeg, app_settings.input_dir, needs_review=True)
    provider = fake_provider(_RICH_EXTRACTION)
    pipeline = Pipeline(app_settings, provider, session_factory, metadata_writer=_RecordingWriter())

    result = await pipeline.process_pair(pair)
    assert result.status is ScanStatus.NEEDS_REVIEW


async def test_tesseract_fallback_marks_needs_review(
    app_settings, session_factory, fake_provider, make_jpeg, monkeypatch
):
    app_settings.input_dir.mkdir(parents=True, exist_ok=True)
    pair = _make_pair(make_jpeg, app_settings.input_dir)
    provider = fake_provider(fail=True)
    monkeypatch.setattr(back_module, "ocr_with_tesseract", lambda _bytes: "OCR Rohtext")
    pipeline = Pipeline(app_settings, provider, session_factory, metadata_writer=_RecordingWriter())

    result = await pipeline.process_pair(pair)

    assert result.status is ScanStatus.NEEDS_REVIEW
    assert result.used_fallback is True
    with session_factory() as session:
        record = session.get(ScanRecord, "pair-1")
    assert record.extraction_fallback is True
    assert record.ai_provider == "tesseract"
    assert record.error is not None  # the original provider error is kept


async def test_process_pair_failure_is_recorded(
    app_settings, session_factory, fake_provider
):
    app_settings.input_dir.mkdir(parents=True, exist_ok=True)
    garbage = app_settings.input_dir / "broken.jpg"
    garbage.write_bytes(b"not an image")
    pair = ScanPair(
        pair_id="pair-bad",
        front=PageRef(garbage),
        back=None,
        method=DetectionMethod.SINGLE_IMAGE,
        source_files=(garbage,),
    )
    pipeline = Pipeline(
        app_settings, fake_provider(), session_factory, metadata_writer=_RecordingWriter()
    )

    result = await pipeline.process_pair(pair)

    assert result.status is ScanStatus.FAILED
    assert result.error is not None
    with session_factory() as session:
        record = session.get(ScanRecord, "pair-bad")
    assert record.status is ScanStatus.FAILED
    assert record.error is not None


# --- processing history register --------------------------------------------


async def test_success_and_failure_append_history_events(
    app_settings, session_factory, fake_provider, make_jpeg
):
    from sqlmodel import select

    from albumine.db import ProcessingEvent

    app_settings.input_dir.mkdir(parents=True, exist_ok=True)
    pair = _make_pair(make_jpeg, app_settings.input_dir)
    provider = fake_provider(_RICH_EXTRACTION)
    pipeline = Pipeline(app_settings, provider, session_factory, metadata_writer=_RecordingWriter())

    await pipeline.process_pair(pair)
    # skip (already DONE) must not append an event; force re-run must.
    await pipeline.process_pair(pair)
    await pipeline.process_pair(pair, force=True)

    garbage = app_settings.input_dir / "broken.jpg"
    garbage.write_bytes(b"not an image")
    bad = ScanPair(
        pair_id="pair-bad",
        front=PageRef(garbage),
        back=None,
        method=DetectionMethod.SINGLE_IMAGE,
        source_files=(garbage,),
    )
    await pipeline.process_pair(bad)

    with session_factory() as session:
        events = session.exec(
            select(ProcessingEvent).order_by(ProcessingEvent.id)
        ).all()

    assert [(e.pair_id, e.action) for e in events] == [
        ("pair-1", "processed"),
        ("pair-1", "processed"),
        ("pair-bad", "failed"),
    ]
    assert events[0].status == "done"
    assert events[0].ai_provider == "fake"
    assert events[0].ai_model == "fake-vision-1"
    assert events[2].detail  # the error text is recorded


async def test_process_pair_records_enhancement_level(
    app_settings, session_factory, fake_provider, make_jpeg
):
    app_settings.input_dir.mkdir(parents=True, exist_ok=True)
    pair = _make_pair(make_jpeg, app_settings.input_dir)
    pipeline = Pipeline(
        app_settings, fake_provider(_RICH_EXTRACTION), session_factory,
        metadata_writer=_RecordingWriter(),
    )

    # app_settings defaults default_enhancement_level to "basic".
    result = await pipeline.process_pair(pair)
    assert result.enhancement_level is EnhancementLevel.BASIC

    with session_factory() as session:
        record = session.get(ScanRecord, "pair-1")
    assert record.enhancement_level == "basic"


async def test_process_pair_enhancement_level_override(
    app_settings, session_factory, fake_provider, make_jpeg
):
    app_settings.input_dir.mkdir(parents=True, exist_ok=True)
    pair = _make_pair(make_jpeg, app_settings.input_dir)
    pipeline = Pipeline(
        app_settings, fake_provider(_RICH_EXTRACTION), session_factory,
        metadata_writer=_RecordingWriter(),
    )

    result = await pipeline.process_pair(pair, enhancement_level=EnhancementLevel.NONE)
    assert result.enhancement_level is EnhancementLevel.NONE


async def test_process_directory(app_settings, session_factory, fake_provider, make_jpeg):
    app_settings.input_dir.mkdir(parents=True, exist_ok=True)
    make_jpeg(app_settings.input_dir / "foto_001a.jpg")
    make_jpeg(app_settings.input_dir / "foto_001b.jpg")
    pipeline = Pipeline(
        app_settings, fake_provider(_RICH_EXTRACTION), session_factory,
        metadata_writer=_RecordingWriter(),
    )

    results = await pipeline.process_directory()

    assert len(results) == 1
    assert results[0].status is ScanStatus.DONE
