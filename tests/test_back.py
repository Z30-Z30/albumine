"""Tests for back-side processing (OCR orchestrator + Tesseract fallback)."""

import pytest

from albumine.ai.base import AIProviderError, BackExtraction
from albumine.ingest.models import PageRef
from albumine.processing import back as back_module
from albumine.processing.back import TesseractError, extract_back


async def test_extract_back_uses_provider(make_jpeg, tmp_path, fake_provider):
    image = make_jpeg(tmp_path / "back.jpg")
    provider = fake_provider(BackExtraction(raw_text="Mai 1973, Zürich"))

    result = await extract_back(PageRef(image), provider)

    assert result.used_fallback is False
    assert result.provider_error is None
    assert result.extraction.raw_text == "Mai 1973, Zürich"
    assert provider.calls == 1


async def test_extract_back_falls_back_to_tesseract(
    make_jpeg, tmp_path, fake_provider, monkeypatch
):
    image = make_jpeg(tmp_path / "back.jpg")
    provider = fake_provider(fail=True)
    monkeypatch.setattr(
        back_module, "ocr_with_tesseract", lambda _bytes: "OCR Rohtext"
    )

    result = await extract_back(PageRef(image), provider)

    assert result.used_fallback is True
    assert result.extraction.raw_text == "OCR Rohtext"
    assert result.provider_error is not None


async def test_extract_back_fallback_disabled_reraises(
    make_jpeg, tmp_path, fake_provider
):
    image = make_jpeg(tmp_path / "back.jpg")
    provider = fake_provider(fail=True)

    with pytest.raises(AIProviderError):
        await extract_back(PageRef(image), provider, allow_fallback=False)


async def test_extract_back_raises_when_fallback_also_fails(
    make_jpeg, tmp_path, fake_provider, monkeypatch
):
    image = make_jpeg(tmp_path / "back.jpg")
    provider = fake_provider(fail=True)

    def _broken_ocr(_bytes):
        raise TesseractError("tesseract binary not found")

    monkeypatch.setattr(back_module, "ocr_with_tesseract", _broken_ocr)

    with pytest.raises(AIProviderError):
        await extract_back(PageRef(image), provider)
