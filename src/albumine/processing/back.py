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
    """Load a page (image or PDF page) and return it as in-memory JPEG bytes."""
    image = load_source(page_ref).convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=95)
    return buffer.getvalue()
