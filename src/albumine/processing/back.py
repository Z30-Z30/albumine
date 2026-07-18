"""Back-side (annotation) processing — the OCR orchestrator.

Primary path: hand the back image to the configured vision-LLM provider, which
returns structured data. Resilience requirement: if the provider is down, fall
back to Tesseract OCR so processing still produces *something* (the raw text),
and flag the pair so it can be re-processed once the backend is healthy again.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image

from albumine.ai.base import AIProviderError, BackExtraction, VisionProvider
from albumine.ingest.models import PageRef
from albumine.logging import get_logger
from albumine.processing.front import load_source

_log = get_logger(__name__)

_TESSERACT_LANG = "deu"

# Orientation probing: scans of photo backs often land sideways or upside down
# in the scanner. Before handing the image to the vision provider we OCR a
# downscaled copy in all four orientations and keep the one with the most
# legible text — but only when the winner is clear, so an already-correct scan
# is never touched.
_ORIENT_MAX_DIM = 1200
_ORIENT_MIN_SCORE = 10
_ORIENT_MIN_ADVANTAGE = 1.5

# Cap the image sent to the vision provider: full-resolution scans blow up the
# vision token count (Ollama rejects requests beyond num_ctx), and handwriting
# stays perfectly legible at this size.
_AI_MAX_DIM = 1568


@dataclass
class BackResult:
    """Outcome of back-side extraction.

    Attributes:
        extraction: The structured data (rich from the LLM, or raw-text-only
            from the Tesseract fallback).
        used_fallback: True when the Tesseract fallback produced this result.
        provider_error: The vision-provider error message, if the primary path
            failed (set together with ``used_fallback``).
    """

    extraction: BackExtraction
    used_fallback: bool
    provider_error: str | None = None


async def extract_back(
    page_ref: PageRef,
    provider: VisionProvider,
    *,
    allow_fallback: bool = True,
) -> BackResult:
    """Extract structured info from a photo back.

    Tries ``provider`` first; on :class:`AIProviderError` falls back to
    Tesseract (unless ``allow_fallback`` is False).

    Raises:
        AIProviderError: If the provider fails and the fallback is disabled or
            also fails.
    """
    image_bytes = _load_jpeg_bytes(page_ref)

    try:
        extraction = await provider.extract_back(image_bytes, mime_type="image/jpeg")
        return BackResult(extraction=extraction, used_fallback=False)
    except AIProviderError as provider_exc:
        if not allow_fallback:
            raise
        _log.warning(
            "back.provider_failed",
            provider=provider.name,
            error=str(provider_exc),
            fallback="tesseract",
        )
        try:
            raw_text = ocr_with_tesseract(image_bytes)
        except TesseractError as tesseract_exc:
            raise AIProviderError(
                f"vision provider failed ({provider_exc}) and the Tesseract "
                f"fallback is unavailable ({tesseract_exc})"
            ) from tesseract_exc
        # Tesseract only gives us the raw text — no structured fields, low
        # confidence. The pair stays flagged for re-processing.
        return BackResult(
            extraction=BackExtraction(raw_text=raw_text),
            used_fallback=True,
            provider_error=str(provider_exc),
        )


def normalize_orientation(image: Image.Image) -> tuple[Image.Image, int]:
    """Rotate a back scan so its text reads upright.

    Scores all four orientations on a downscaled copy and rotates only when a
    non-zero orientation is clearly better than the original. Returns the
    (possibly rotated) image and the applied rotation in degrees
    (counter-clockwise; 0 = unchanged).
    """
    probe = image.copy()
    probe.thumbnail((_ORIENT_MAX_DIM, _ORIENT_MAX_DIM))

    scores: dict[int, int] = {}
    for angle in (0, 90, 180, 270):
        rotated = probe if angle == 0 else probe.rotate(angle, expand=True)
        score = _score_text(rotated)
        if score is None:  # Tesseract unavailable — leave the image alone
            return image, 0
        scores[angle] = score

    best = max(scores, key=lambda a: scores[a])
    if (
        best == 0
        or scores[best] < _ORIENT_MIN_SCORE
        or scores[best] < _ORIENT_MIN_ADVANTAGE * max(scores[0], 1)
    ):
        return image, 0
    return image.rotate(best, expand=True), best


def _score_text(image: Image.Image) -> int | None:
    """How much legible text Tesseract sees in ``image``; None if unavailable.

    The score is the total character count of words recognised with reasonable
    confidence — enough signal to compare orientations even on handwriting.
    """
    try:
        import pytesseract
    except ImportError:
        return None

    try:
        data = pytesseract.image_to_data(
            image, lang=_TESSERACT_LANG, output_type=pytesseract.Output.DICT
        )
    except Exception:
        return None
    return sum(
        len(word.strip())
        for word, conf in zip(data["text"], data["conf"], strict=False)
        if word.strip() and float(conf) > 30
    )


class TesseractError(RuntimeError):
    """Raised when Tesseract OCR is unavailable or fails."""


def ocr_with_tesseract(image_bytes: bytes) -> str:
    """Run Tesseract OCR over an image and return the recognised text.

    Raises:
        TesseractError: If Tesseract is not installed or errors out.
    """
    try:
        import pytesseract
    except ImportError as exc:  # pragma: no cover - dependency present in prod
        raise TesseractError("pytesseract is not installed") from exc

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            text = pytesseract.image_to_string(image, lang=_TESSERACT_LANG)
    except pytesseract.TesseractNotFoundError as exc:
        raise TesseractError("tesseract binary not found") from exc
    except (pytesseract.TesseractError, OSError) as exc:
        raise TesseractError(f"tesseract failed: {exc}") from exc
    return text.strip()


def _load_jpeg_bytes(page_ref: PageRef) -> bytes:
    """Load a page (image or PDF page) and return it as in-memory JPEG bytes.

    Downscales to at most ``_AI_MAX_DIM`` on the longest side — the bytes go
    to the vision provider (and the Tesseract fallback), not to the archive.
    """
    image = load_source(page_ref).convert("RGB")
    image, degrees = normalize_orientation(image)
    if degrees:
        _log.info(
            "back.orientation_corrected", source=str(page_ref.path), degrees=degrees
        )
    if max(image.size) > _AI_MAX_DIM:
        original = image.size
        image.thumbnail((_AI_MAX_DIM, _AI_MAX_DIM))
        _log.info(
            "back.downscaled_for_ai",
            source=str(page_ref.path),
            original=f"{original[0]}x{original[1]}",
            sent=f"{image.size[0]}x{image.size[1]}",
        )
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=95)
    return buffer.getvalue()
