"""Shared test configuration and fixtures.

The default volume paths (``/input`` etc.) only exist inside the container.
Point them at a temporary directory before any app code reads the settings.
"""

import os
import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest

_tmp = tempfile.mkdtemp(prefix="albumine-test-")
os.environ.setdefault("ALBUMINE_INPUT_DIR", os.path.join(_tmp, "input"))
os.environ.setdefault("ALBUMINE_OUTPUT_DIR", os.path.join(_tmp, "output"))
os.environ.setdefault("ALBUMINE_CONFIG_DIR", os.path.join(_tmp, "config"))


@pytest.fixture
def make_pdf() -> Callable[[Path, int], Path]:
    """Return a factory that writes a blank multi-page PDF and returns its path."""
    from pypdf import PdfWriter

    def _make(path: Path, pages: int) -> Path:
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=200, height=200)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            writer.write(handle)
        return path

    return _make


@pytest.fixture
def make_image() -> Callable[[Path, bytes], Path]:
    """Return a factory that writes a file with arbitrary bytes and returns it.

    Pair detection only inspects file names/extensions, so the content does not
    need to be a valid image — but it must exist for content hashing.
    """

    def _make(path: Path, content: bytes = b"fake-image-bytes") -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    return _make
