"""Domain types for the ingest stage.

These are plain in-memory models — persistence to SQLite arrives with the
end-to-end pipeline in a later phase.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

# File extensions we treat as photo scans / PDF scans. Anything else in the
# watch-folder is ignored.
IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".webp"}
)
PDF_EXTENSIONS: frozenset[str] = frozenset({".pdf"})


class DetectionMethod(StrEnum):
    """How a :class:`ScanPair` was derived from the source files."""

    PDF_DUPLEX = "pdf_duplex"      # 2-page PDF: page 1 front, page 2 back
    PDF_MULTI = "pdf_multi"        # N×2-page PDF: alternating front/back
    IMAGE_PAIR = "image_pair"      # two images via naming convention (…a / …b)
    SINGLE_IMAGE = "single_image"  # a lone image, front only, no back
    SINGLE_PDF = "single_pdf"      # a 1-page PDF, front only, no back
    AMBIGUOUS = "ambiguous"        # cannot decide automatically — needs manual override


@dataclass(frozen=True)
class PageRef:
    """A reference to one page of source material.

    Attributes:
        path: The source file on disk.
        page_index: 0-based page number inside a PDF, or ``None`` for a
            standalone image file.
    """

    path: Path
    page_index: int | None = None

    def __str__(self) -> str:
        if self.page_index is None:
            return self.path.name
        return f"{self.path.name}#p{self.page_index + 1}"


@dataclass
class ScanPair:
    """A detected front/back pair (or front-only item) ready for processing.

    Attributes:
        pair_id: Stable, content-derived identifier — re-ingesting the same
            source material yields the same id (idempotency).
        front: The photo side.
        back: The annotated reverse side, or ``None`` if there is none.
        method: Which heuristic produced this pair.
        source_files: The original file(s) this pair was derived from.
        needs_review: True when the heuristic was not confident and a human
            should confirm the pairing in the web UI.
        note: Human-readable explanation, set when ``needs_review`` is True.
    """

    pair_id: str
    front: PageRef
    back: PageRef | None
    method: DetectionMethod
    source_files: tuple[Path, ...]
    needs_review: bool = False
    note: str | None = None
