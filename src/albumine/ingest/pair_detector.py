"""Pair detection — group raw scan files into front/back :class:`ScanPair`s.

Heuristics, in priority order:

1. **PDF, 2 pages**      → page 1 = front, page 2 = back (``PDF_DUPLEX``).
2. **PDF, N×2 pages**    → alternating front/back, pairs (1,2), (3,4), …
   (``PDF_MULTI``).
3. **PDF, 1 page**       → front only, no back (``SINGLE_PDF``).
4. **PDF, odd > 1 pages**→ cannot split cleanly → ``AMBIGUOUS``, needs review.
5. **Image naming pair** → two images sharing a base name with side markers
   ``a``/``b`` (after a digit) or ``front``/``back`` (after a separator)
   → ``IMAGE_PAIR``.
6. **Duplex-scanner suffix pair** → ``BASE.jpg`` + ``BASE_001.jpg`` (the
   convention of document scanners: front, then back as ``_001``)
   → ``IMAGE_PAIR``. Only applies when *both* files exist; a lone ``X_001``
   stays a single image (it may just be sequence numbering).
7. **Lone image, no marker** → front only (``SINGLE_IMAGE``).
8. **Image with a side marker but no partner / conflicting markers**
   → ``AMBIGUOUS``, needs review.

Anything flagged ``needs_review`` is left for the user to confirm or correct in
the web UI (manual override).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from albumine.ingest.hashing import make_pair_id
from albumine.ingest.models import (
    IMAGE_EXTENSIONS,
    PDF_EXTENSIONS,
    DetectionMethod,
    PageRef,
    ScanPair,
)
from albumine.ingest.pdf_splitter import PdfReadError, count_pages
from albumine.logging import get_logger

_log = get_logger(__name__)

# Side markers. A bare trailing letter only counts when it follows a digit
# (e.g. "foto_001a"); word markers only count after a separator (e.g.
# "foto_001_front"). This keeps names like "banana" from being mis-parsed.
_SIDE_AFTER_DIGIT = re.compile(r"^(?P<base>.+\d)(?P<side>[ab])$", re.IGNORECASE)
_SIDE_AFTER_SEP = re.compile(
    r"^(?P<base>.+)[ _-](?P<side>front|back|vorne|hinten|vorderseite|rueckseite|a|b)$",
    re.IGNORECASE,
)
_FRONT_TOKENS = {"a", "front", "vorne", "vorderseite"}
_BACK_TOKENS = {"b", "back", "hinten", "rueckseite"}

# Duplex-scanner suffix: document scanners emit "BASE.jpg" for the front and
# "BASE_001.jpg" for the back of the same sheet. The suffix alone is a weak
# signal (it may be plain sequence numbering), so it only pairs when the
# matching base file exists next to it.
_NUMERIC_SUFFIX = re.compile(r"^(?P<base>.+?)_(?P<num>\d{3})$")


def detect_pairs(paths: Iterable[Path]) -> list[ScanPair]:
    """Group an arbitrary set of files into :class:`ScanPair` objects.

    Non-media files are ignored. The result is sorted deterministically so the
    same input always yields the same ordering.
    """
    pdfs: list[Path] = []
    images: list[Path] = []
    for path in paths:
        suffix = path.suffix.lower()
        if suffix in PDF_EXTENSIONS:
            pdfs.append(path)
        elif suffix in IMAGE_EXTENSIONS:
            images.append(path)

    pairs: list[ScanPair] = []
    for pdf in sorted(pdfs):
        pairs.extend(_pairs_from_pdf(pdf))
    pairs.extend(_pairs_from_images(images))

    pairs.sort(key=lambda p: (str(p.front.path), p.front.page_index or 0))
    _log.info(
        "pairs.detected",
        total=len(pairs),
        needs_review=sum(1 for p in pairs if p.needs_review),
    )
    return pairs


def scan_directory(folder: Path) -> list[ScanPair]:
    """Convenience wrapper: list media files in ``folder`` and detect pairs."""
    if not folder.is_dir():
        return []
    return detect_pairs(p for p in sorted(folder.rglob("*")) if p.is_file())


# --- PDF handling -----------------------------------------------------------


def _pairs_from_pdf(pdf: Path) -> list[ScanPair]:
    try:
        pages = count_pages(pdf)
    except PdfReadError as exc:
        _log.warning("pdf.unreadable", source=str(pdf), error=str(exc))
        return [_pair(PageRef(pdf, 0), None, DetectionMethod.AMBIGUOUS, (pdf,),
                      note=f"PDF konnte nicht gelesen werden: {exc}")]

    if pages == 0:
        _log.warning("pdf.empty", source=str(pdf))
        return []

    if pages == 1:
        return [_pair(PageRef(pdf, 0), None, DetectionMethod.SINGLE_PDF, (pdf,))]

    if pages == 2:
        return [_pair(PageRef(pdf, 0), PageRef(pdf, 1),
                      DetectionMethod.PDF_DUPLEX, (pdf,))]

    if pages % 2 == 0:
        return [
            _pair(PageRef(pdf, i), PageRef(pdf, i + 1),
                  DetectionMethod.PDF_MULTI, (pdf,))
            for i in range(0, pages, 2)
        ]

    # Odd page count > 1: front/back assignment is ambiguous.
    return [_pair(PageRef(pdf, 0), None, DetectionMethod.AMBIGUOUS, (pdf,),
                  note=f"PDF hat {pages} Seiten (ungerade) — Front/Back-Zuordnung "
                       "unklar, bitte manuell prüfen")]


# --- Image handling ---------------------------------------------------------


def _parse_side(stem: str) -> tuple[str, str] | None:
    """Return ``(base_name, "front"|"back")`` if the stem carries a side marker."""
    for pattern in (_SIDE_AFTER_DIGIT, _SIDE_AFTER_SEP):
        match = pattern.match(stem)
        if match:
            token = match.group("side").lower()
            side = "front" if token in _FRONT_TOKENS else "back"
            return match.group("base"), side
    return None


def _pairs_from_images(images: Iterable[Path]) -> list[ScanPair]:
    singles: list[Path] = []
    # group key -> side -> list of files
    groups: dict[tuple[Path, str], dict[str, list[Path]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for image in images:
        parsed = _parse_side(image.stem)
        if parsed is None:
            singles.append(image)
            continue
        base, side = parsed
        groups[(image.parent, base.lower())][side].append(image)

    pairs: list[ScanPair] = []
    for group in groups.values():
        pairs.extend(_resolve_image_group(group))
    pairs.extend(_pairs_from_numeric_suffixes(singles))
    return pairs


def _pairs_from_numeric_suffixes(singles: list[Path]) -> list[ScanPair]:
    """Pair ``BASE`` + ``BASE_001`` duplex-scanner files among marker-less images.

    Images that take part in no such pair are emitted as ``SINGLE_IMAGE``.
    """
    by_stem = {(img.parent, img.stem.lower()): img for img in singles}
    suffixed: dict[tuple[Path, str], list[Path]] = defaultdict(list)
    for img in singles:
        match = _NUMERIC_SUFFIX.match(img.stem)
        if match:
            base_key = (img.parent, match.group("base").lower())
            if base_key in by_stem:
                suffixed[base_key].append(img)

    pairs: list[ScanPair] = []
    claimed: set[Path] = set()
    for base_key, kids in sorted(suffixed.items(), key=lambda kv: str(kv[0][1])):
        front = by_stem[base_key]
        kids = [k for k in kids if k not in claimed]
        if front in claimed or not kids:
            continue
        if len(kids) == 1 and kids[0].stem.lower().endswith("_001"):
            back = kids[0]
            claimed.update((front, back))
            pairs.append(_pair(PageRef(front), PageRef(back),
                               DetectionMethod.IMAGE_PAIR, (front, back)))
        else:
            # More than one numbered sibling (X_001, X_002, …): the sheet
            # assignment is unclear — surface every file for manual review.
            claimed.update((front, *kids))
            for image in sorted([front, *kids]):
                pairs.append(_pair(PageRef(image), None,
                                   DetectionMethod.AMBIGUOUS, (image,),
                                   note="Mehrere nummerierte Scan-Dateien zur "
                                        "selben Basis — bitte Zuordnung manuell "
                                        "festlegen"))
    for image in singles:
        if image not in claimed:
            pairs.append(_pair(PageRef(image), None, DetectionMethod.SINGLE_IMAGE,
                               (image,)))
    return pairs


def _resolve_image_group(group: dict[str, list[Path]]) -> list[ScanPair]:
    fronts = group.get("front", [])
    backs = group.get("back", [])

    if len(fronts) == 1 and len(backs) == 1:
        front, back = fronts[0], backs[0]
        return [_pair(PageRef(front), PageRef(back), DetectionMethod.IMAGE_PAIR,
                      (front, back))]

    if len(fronts) == 1 and not backs:
        front = fronts[0]
        return [_pair(PageRef(front), None, DetectionMethod.AMBIGUOUS, (front,),
                      note="Vorderseite ohne passende Rückseite gefunden")]

    if len(backs) == 1 and not fronts:
        back = backs[0]
        return [_pair(PageRef(back), None, DetectionMethod.AMBIGUOUS, (back,),
                      note="Rückseiten-Datei (Marker 'b'/'back') ohne passende "
                           "Vorderseite gefunden")]

    # Conflicting markers (e.g. two fronts, or more than one of each):
    # surface every file individually so nothing is silently dropped.
    conflicting = sorted(fronts + backs)
    return [
        _pair(PageRef(image), None, DetectionMethod.AMBIGUOUS, (image,),
              note="Mehrdeutige Seiten-Marker in der Datei-Gruppe — bitte "
                   "Zuordnung manuell festlegen")
        for image in conflicting
    ]


# --- helpers ----------------------------------------------------------------


def _pair(
    front: PageRef,
    back: PageRef | None,
    method: DetectionMethod,
    source_files: tuple[Path, ...],
    *,
    note: str | None = None,
) -> ScanPair:
    return ScanPair(
        pair_id=make_pair_id(front, back, source_files),
        front=front,
        back=back,
        method=method,
        source_files=source_files,
        needs_review=method is DetectionMethod.AMBIGUOUS,
        note=note,
    )
