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

    def as_dict(self) -> dict[str, object]:
        """Plain-dict form for serialisation (e.g. across the task queue)."""
        return {"path": str(self.path), "page_index": self.page_index}

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> PageRef:
        """Inverse of :meth:`as_dict`."""
        page_index = data["page_index"]
        return cls(
            path=Path(str(data["path"])),
            page_index=int(page_index) if page_index is not None else None,
        )


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

    def as_dict(self) -> dict[str, object]:
        """Plain-dict form for serialisation (e.g. across the ARQ task queue)."""
        return {
            "pair_id": self.pair_id,
            "front": self.front.as_dict(),
            "back": self.back.as_dict() if self.back is not None else None,
            "method": str(self.method),
            "source_files": [str(p) for p in self.source_files],
            "needs_review": self.needs_review,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ScanPair:
        """Inverse of :meth:`as_dict`."""
        back = data["back"]
        return cls(
            pair_id=str(data["pair_id"]),
            front=PageRef.from_dict(data["front"]),  # type: ignore[arg-type]
            back=PageRef.from_dict(back) if back is not None else None,  # type: ignore[arg-type]
            method=DetectionMethod(data["method"]),
            source_files=tuple(Path(p) for p in data["source_files"]),  # type: ignore[union-attr]
            needs_review=bool(data["needs_review"]),
            note=data["note"],  # type: ignore[arg-type]
        )
