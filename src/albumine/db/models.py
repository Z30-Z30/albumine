"""Database models.

A single :class:`ScanRecord` table tracks every detected scan pair through the
pipeline: detection result, extracted metadata, output location and processing
history. The ``pair_id`` primary key (content-derived, see
:mod:`albumine.ingest.hashing`) gives us idempotency for free — re-ingesting the
same source material updates the existing row instead of creating a duplicate.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel


class ScanStatus(StrEnum):
    """Lifecycle state of a scan pair."""

    PENDING = "pending"            # detected, not yet processed
    PROCESSING = "processing"      # currently being worked on
    DONE = "done"                  # processed successfully
    NEEDS_REVIEW = "needs_review"  # processed, but a human should confirm
    FAILED = "failed"              # processing errored out


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ScanRecord(SQLModel, table=True):
    """One detected scan pair and everything the pipeline learned about it.

    List-valued fields (``source_files``, ``people``) are stored as JSON text —
    SQLite has no array type and the helper properties keep callers ergonomic.
    """

    __tablename__ = "scan_records"

    pair_id: str = Field(primary_key=True)
    status: ScanStatus = Field(default=ScanStatus.PENDING, index=True)

    # --- detection -----------------------------------------------------------
    detection_method: str
    needs_review: bool = False
    review_note: str | None = None

    # --- source material -----------------------------------------------------
    front_path: str
    front_page_index: int | None = None
    back_path: str | None = None
    back_page_index: int | None = None
    source_files_json: str = "[]"

    # --- output --------------------------------------------------------------
    output_path: str | None = None

    # --- extracted metadata --------------------------------------------------
    raw_text: str | None = None
    description: str | None = None
    date_iso: str | None = None
    date_original_text: str | None = None
    date_confidence: str | None = None
    date_precision: str | None = None
    location: str | None = None
    people_json: str = "[]"
    event: str | None = None
    notes: str | None = None

    # --- provenance ----------------------------------------------------------
    ai_provider: str | None = None
    ai_model: str | None = None
    enhancement_level: str = "none"  # the level actually applied
    extraction_fallback: bool = False  # Tesseract fallback was used
    error: str | None = None

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    # --- JSON-list convenience accessors ------------------------------------

    @property
    def source_files(self) -> list[str]:
        return json.loads(self.source_files_json)

    @source_files.setter
    def source_files(self, value: list[str]) -> None:
        self.source_files_json = json.dumps(value)

    @property
    def people(self) -> list[str]:
        return json.loads(self.people_json)

    @people.setter
    def people(self, value: list[str]) -> None:
        self.people_json = json.dumps(value)

    def touch(self) -> None:
        """Bump ``updated_at`` to now."""
        self.updated_at = _utcnow()
