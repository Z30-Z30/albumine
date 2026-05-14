"""Tests for the image-enhancement pipeline.

The ``basic`` level is tested for real (Pillow/OpenCV). The ``enhance`` /
``restore`` levels are tested via a stub binary that mimics the
``-i <in> -o <out>`` CLI contract of Real-ESRGAN / GFPGAN — this exercises the
subprocess wrapper and graceful degradation without the heavy ML tools.
"""

import stat

import pytest
from PIL import Image

from albumine.config import EnhancementLevel, Settings
from albumine.processing.enhance import apply_enhancement


@pytest.fixture
def make_tool_stub():
    """Return a factory that writes a fake enhancement CLI binary.

    The stub honours the ``-i``/``-o`` contract: it copies input to output
    (``ok``) or exits non-zero (``fail``).
    """

    def _make(path, *, behaviour="ok"):
        if behaviour == "ok":
            body = (
                "#!/bin/sh\n"
                'while [ $# -gt 0 ]; do\n'
                '  case "$1" in\n'
                '    -i) IN="$2"; shift 2;;\n'
                '    -o) OUT="$2"; shift 2;;\n'
                "    *) shift;;\n"
                "  esac\n"
                "done\n"
                'cp "$IN" "$OUT"\n'
            )
        else:
            body = "#!/bin/sh\nexit 1\n"
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return path

    return _make


def _photo() -> Image.Image:
    return Image.new("RGB", (64, 48), color=(160, 120, 70))


def test_level_none_returns_image_unchanged():
    image = _photo()
    result, applied = apply_enhancement(image, EnhancementLevel.NONE, settings=Settings())
    assert result is image
    assert applied is EnhancementLevel.NONE


def test_basic_enhancement_runs():
    result, applied = apply_enhancement(_photo(), EnhancementLevel.BASIC, settings=Settings())
    assert applied is EnhancementLevel.BASIC
    assert result.mode == "RGB"
    assert result.size == (64, 48)


def test_enhance_degrades_to_basic_without_realesrgan():
    # No realesrgan_bin configured -> can't reach 'enhance', degrades to 'basic'.
    settings = Settings(realesrgan_bin=None)
    _, applied = apply_enhancement(_photo(), EnhancementLevel.ENHANCE, settings=settings)
    assert applied is EnhancementLevel.BASIC


def test_restore_degrades_when_no_tools():
    settings = Settings(realesrgan_bin=None, gfpgan_bin=None)
    _, applied = apply_enhancement(_photo(), EnhancementLevel.RESTORE, settings=settings)
    assert applied is EnhancementLevel.BASIC


def test_enhance_uses_realesrgan_stub(make_tool_stub, tmp_path):
    stub = make_tool_stub(tmp_path / "realesrgan")
    settings = Settings(realesrgan_bin=str(stub))

    result, applied = apply_enhancement(_photo(), EnhancementLevel.ENHANCE, settings=settings)
    assert applied is EnhancementLevel.ENHANCE
    assert result.mode == "RGB"


def test_restore_uses_both_stubs(make_tool_stub, tmp_path):
    settings = Settings(
        realesrgan_bin=str(make_tool_stub(tmp_path / "realesrgan")),
        gfpgan_bin=str(make_tool_stub(tmp_path / "gfpgan")),
    )
    _, applied = apply_enhancement(_photo(), EnhancementLevel.RESTORE, settings=settings)
    assert applied is EnhancementLevel.RESTORE


def test_restore_degrades_to_enhance_when_gfpgan_missing(make_tool_stub, tmp_path):
    settings = Settings(
        realesrgan_bin=str(make_tool_stub(tmp_path / "realesrgan")),
        gfpgan_bin=None,
    )
    _, applied = apply_enhancement(_photo(), EnhancementLevel.RESTORE, settings=settings)
    assert applied is EnhancementLevel.ENHANCE


def test_failing_tool_degrades_gracefully(make_tool_stub, tmp_path):
    # A configured but failing binary must not crash the pipeline.
    stub = make_tool_stub(tmp_path / "realesrgan", behaviour="fail")
    settings = Settings(realesrgan_bin=str(stub))

    _, applied = apply_enhancement(_photo(), EnhancementLevel.ENHANCE, settings=settings)
    assert applied is EnhancementLevel.BASIC


def test_missing_tool_binary_degrades_gracefully():
    settings = Settings(realesrgan_bin="/nonexistent/path/to/realesrgan")
    _, applied = apply_enhancement(_photo(), EnhancementLevel.ENHANCE, settings=settings)
    assert applied is EnhancementLevel.BASIC
