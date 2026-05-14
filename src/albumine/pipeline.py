"""End-to-end processing pipeline.

Wires the stages together for one detected scan pair:

    front-image processing  ─┐
    back AI extraction       ├─►  date reconciliation  ─►  metadata write  ─►  DB
    (Tesseract fallback)    ─┘

Idempotency: the pipeline is keyed on ``pair_id`` (content-derived). A pair that
is already ``DONE`` is skipped unless ``force=True``.

Resilience: if the vision backend is down, back extraction falls back to
Tesseract and the pair is marked ``NEEDS_REVIEW`` for later re-processing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from albumine.ai.base import AIProviderError, BackExtraction, ExtractedDate, VisionProvider
from albumine.config import Settings
from albumine.db import ScanRecord, ScanStatus
from albumine.db.engine import SessionFactory
from albumine.ingest import ScanPair, scan_directory
from albumine.logging import get_logger
from albumine.parsing.date_parser import (
    DatePrecision,
    ParsedDate,
    parse_date,
    weakest_confidence,
)
from albumine.processing.back import extract_back
from albumine.processing.front import FrontProcessingError, process_front, save_image
from albumine.processing.metadata_writer import (
    ExifToolError,
    PhotoMetadata,
    write_metadata,
)

_log = get_logger(__name__)

#: Injected metadata-writer signature: ``(image_path, metadata, sidecar)``.
MetadataWriter = Callable[[Path, PhotoMetadata, bool], None]


@dataclass
class PipelineResult:
    """Summary of processing one scan pair."""

    pair_id: str
    status: ScanStatus
    output_path: Path | None
    used_fallback: bool
    error: str | None = None


def reconcile_date(extracted: ExtractedDate) -> ParsedDate:
    """Combine the vision model's date reading with the deterministic parser.

    The model gives us ``original_text`` (and its own ``iso``/``confidence``).
    The trustworthy EXIF timestamp comes from running the tested
    :func:`albumine.parsing.date_parser.parse_date` over that text. The final
    confidence is the *weaker* of the two signals — the parser can only be as
    reliable as the text the model managed to read.
    """
    parsed = parse_date(extracted.original_text)
    if parsed.precision is DatePrecision.NONE and extracted.iso:
        parsed = parse_date(extracted.iso)
    if parsed.precision is DatePrecision.NONE:
        return parsed
    combined = weakest_confidence(parsed.confidence, extracted.confidence)
    return replace(parsed, confidence=combined)


def _default_metadata_writer(
    image_path: Path, metadata: PhotoMetadata, sidecar: bool
) -> None:
    write_metadata(image_path, metadata, sidecar=sidecar)


class Pipeline:
    """Orchestrates processing of detected scan pairs."""

    def __init__(
        self,
        settings: Settings,
        provider: VisionProvider,
        session_factory: SessionFactory,
        *,
        metadata_writer: MetadataWriter = _default_metadata_writer,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._session_factory = session_factory
        self._write_metadata = metadata_writer

    async def process_directory(self, *, force: bool = False) -> list[PipelineResult]:
        """Detect and process every scan pair currently in the input folder."""
        pairs = scan_directory(self._settings.input_dir)
        _log.info("pipeline.directory_scan", input=str(self._settings.input_dir), pairs=len(pairs))
        return [await self.process_pair(pair, force=force) for pair in pairs]

    async def process_pair(
        self, pair: ScanPair, *, force: bool = False
    ) -> PipelineResult:
        """Run one scan pair through the full pipeline.

        Returns a :class:`PipelineResult`; never raises for expected processing
        failures — those are recorded on the :class:`ScanRecord` as ``FAILED``.
        """
        if not force and self._already_done(pair.pair_id):
            _log.info("pipeline.skip_done", pair_id=pair.pair_id)
            return PipelineResult(
                pair_id=pair.pair_id,
                status=ScanStatus.DONE,
                output_path=self._output_path_for(pair),
                used_fallback=False,
            )

        self._mark_processing(pair)

        try:
            output_path = self._output_path_for(pair)
            front_image = process_front(pair.front, auto_crop=self._settings.auto_crop)
            self._settings.output_dir.mkdir(parents=True, exist_ok=True)
            save_image(front_image, str(output_path), jpeg_quality=self._settings.jpeg_quality)

            extraction = BackExtraction()
            used_fallback = False
            provider_error: str | None = None
            if pair.back is not None:
                back_result = await extract_back(
                    pair.back,
                    self._provider,
                    allow_fallback=self._settings.ai_fallback_enabled,
                )
                extraction = back_result.extraction
                used_fallback = back_result.used_fallback
                provider_error = back_result.provider_error

            parsed_date = reconcile_date(extraction.date)
            metadata = self._build_metadata(pair, extraction, parsed_date, used_fallback)
            self._write_metadata(output_path, metadata, self._settings.write_sidecar)
        except (FrontProcessingError, AIProviderError, ExifToolError, OSError) as exc:
            self._mark_failed(pair.pair_id, str(exc))
            _log.error("pipeline.failed", pair_id=pair.pair_id, error=str(exc))
            return PipelineResult(
                pair_id=pair.pair_id,
                status=ScanStatus.FAILED,
                output_path=None,
                used_fallback=False,
                error=str(exc),
            )

        status = (
            ScanStatus.NEEDS_REVIEW
            if (pair.needs_review or used_fallback)
            else ScanStatus.DONE
        )
        self._persist_success(
            pair, output_path, extraction, parsed_date, metadata, used_fallback,
            provider_error, status,
        )
        _log.info(
            "pipeline.processed",
            pair_id=pair.pair_id,
            status=status,
            output=str(output_path),
            used_fallback=used_fallback,
        )
        return PipelineResult(
            pair_id=pair.pair_id,
            status=status,
            output_path=output_path,
            used_fallback=used_fallback,
        )

    # --- metadata assembly --------------------------------------------------

    def _build_metadata(
        self,
        pair: ScanPair,
        extraction: BackExtraction,
        parsed_date: ParsedDate,
        used_fallback: bool,
    ) -> PhotoMetadata:
        has_date = parsed_date.precision is not DatePrecision.NONE
        return PhotoMetadata(
            raw_text=extraction.raw_text or None,
            description=_compose_description(extraction),
            location=extraction.location,
            people=list(extraction.people),
            event=extraction.event,
            notes=extraction.notes,
            date=parsed_date if has_date else None,
            ai_provider="tesseract" if used_fallback else self._provider.name,
            ai_model=None if used_fallback else self._provider.model,
            source_files=[str(p) for p in pair.source_files],
        )

    # --- database bookkeeping ----------------------------------------------

    def _already_done(self, pair_id: str) -> bool:
        with self._session_factory() as session:
            record = session.get(ScanRecord, pair_id)
            return record is not None and record.status is ScanStatus.DONE

    def _mark_processing(self, pair: ScanPair) -> None:
        with self._session_factory() as session:
            record = session.get(ScanRecord, pair.pair_id) or ScanRecord(
                pair_id=pair.pair_id, detection_method=str(pair.method), front_path=""
            )
            record.detection_method = str(pair.method)
            record.needs_review = pair.needs_review
            record.review_note = pair.note
            record.front_path = str(pair.front.path)
            record.front_page_index = pair.front.page_index
            record.back_path = str(pair.back.path) if pair.back else None
            record.back_page_index = pair.back.page_index if pair.back else None
            record.source_files = [str(p) for p in pair.source_files]
            record.status = ScanStatus.PROCESSING
            record.error = None
            record.touch()
            session.add(record)
            session.commit()

    def _mark_failed(self, pair_id: str, error: str) -> None:
        with self._session_factory() as session:
            record = session.get(ScanRecord, pair_id)
            if record is None:
                return
            record.status = ScanStatus.FAILED
            record.error = error
            record.touch()
            session.add(record)
            session.commit()

    def _persist_success(
        self,
        pair: ScanPair,
        output_path: Path,
        extraction: BackExtraction,
        parsed_date: ParsedDate,
        metadata: PhotoMetadata,
        used_fallback: bool,
        provider_error: str | None,
        status: ScanStatus,
    ) -> None:
        with self._session_factory() as session:
            record = session.get(ScanRecord, pair.pair_id)
            if record is None:  # pragma: no cover - _mark_processing always runs first
                record = ScanRecord(
                    pair_id=pair.pair_id,
                    detection_method=str(pair.method),
                    front_path=str(pair.front.path),
                )
            record.output_path = str(output_path)
            record.raw_text = extraction.raw_text or None
            record.description = metadata.description
            record.date_iso = parsed_date.iso
            record.date_original_text = parsed_date.original_text or None
            record.date_confidence = str(parsed_date.confidence)
            record.date_precision = str(parsed_date.precision)
            record.location = extraction.location
            record.people = list(extraction.people)
            record.event = extraction.event
            record.notes = extraction.notes
            record.ai_provider = metadata.ai_provider
            record.ai_model = metadata.ai_model
            record.extraction_fallback = used_fallback
            record.error = provider_error
            record.status = status
            record.touch()
            session.add(record)
            session.commit()

    def _output_path_for(self, pair: ScanPair) -> Path:
        front = pair.front
        if front.page_index is None:
            stem = front.path.stem
        else:
            stem = f"{front.path.stem}_p{front.page_index + 1:03d}"
        return self._settings.output_dir / f"{stem}.jpg"


def _compose_description(extraction: BackExtraction) -> str | None:
    """Build a short human-readable description from the structured fields."""
    pieces: list[str] = []

    headline_parts = [extraction.event]
    if extraction.location:
        headline_parts.append(f"in {extraction.location}")
    headline = " ".join(part for part in headline_parts if part).strip()
    if headline:
        pieces.append(f"{headline}.")

    if extraction.people:
        pieces.append("Personen: " + ", ".join(extraction.people) + ".")
    if extraction.notes:
        pieces.append(extraction.notes)

    return " ".join(pieces) if pieces else None
