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


async def test_extract_back_downscales_large_scans(tmp_path, fake_provider):
    import io

    from PIL import Image

    # A full-resolution scan; must not reach the provider at this size.
    path = tmp_path / "back_big.jpg"
    Image.new("RGB", (3200, 2100), color=(230, 230, 220)).save(path, "JPEG")

    received = {}

    provider = fake_provider(BackExtraction(raw_text="ok"))
    original_extract = provider.extract_back

    async def capturing_extract(image, *, mime_type="image/jpeg"):
        received["size"] = Image.open(io.BytesIO(image)).size
        return await original_extract(image, mime_type=mime_type)

    provider.extract_back = capturing_extract
    await extract_back(PageRef(path), provider)

    assert max(received["size"]) <= back_module._AI_MAX_DIM
    # Aspect ratio preserved (3200x2100 -> 1568x1029).
    assert received["size"] == (1568, 1029)


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


# --- Orientation normalisation -----------------------------------------------


def _patch_scores(monkeypatch, scores):
    """Feed normalize_orientation one score per probed angle (0, 90, 180, 270)."""
    remaining = iter(scores)
    monkeypatch.setattr(back_module, "_score_text", lambda _img: next(remaining))


def test_normalize_orientation_rotates_upside_down_scan(monkeypatch):
    from PIL import Image

    _patch_scores(monkeypatch, [0, 2, 40, 1])
    source = Image.new("RGB", (40, 20))

    result, degrees = back_module.normalize_orientation(source)

    assert degrees == 180
    assert result.size == (40, 20)


def test_normalize_orientation_rotates_sideways_scan(monkeypatch):
    from PIL import Image

    _patch_scores(monkeypatch, [1, 35, 0, 3])
    source = Image.new("RGB", (40, 20))

    result, degrees = back_module.normalize_orientation(source)

    assert degrees == 90
    assert result.size == (20, 40)


def test_normalize_orientation_keeps_image_when_no_clear_winner(monkeypatch):
    from PIL import Image

    _patch_scores(monkeypatch, [30, 31, 33, 29])
    source = Image.new("RGB", (40, 20))

    result, degrees = back_module.normalize_orientation(source)

    assert degrees == 0
    assert result is source


def test_normalize_orientation_keeps_image_on_weak_signal(monkeypatch):
    from PIL import Image

    _patch_scores(monkeypatch, [0, 0, 5, 0])  # below _ORIENT_MIN_SCORE
    source = Image.new("RGB", (40, 20))

    _, degrees = back_module.normalize_orientation(source)

    assert degrees == 0


def test_normalize_orientation_without_tesseract_is_a_no_op(monkeypatch):
    from PIL import Image

    monkeypatch.setattr(back_module, "_score_text", lambda _img: None)
    source = Image.new("RGB", (40, 20))

    result, degrees = back_module.normalize_orientation(source)

    assert degrees == 0
    assert result is source
