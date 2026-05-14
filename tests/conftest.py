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
# Tests run without Redis — fail fast instead of retrying for seconds.
os.environ.setdefault("ALBUMINE_REDIS_CONNECT_RETRIES", "1")


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


@pytest.fixture
def make_jpeg() -> Callable[[Path], Path]:
    """Return a factory that writes a tiny valid JPEG and returns its path."""
    from PIL import Image

    def _make(path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 16), color=(120, 90, 60)).save(path, "JPEG")
        return path

    return _make


@pytest.fixture
def make_scan_jpeg() -> Callable[..., Path]:
    """Return a factory that writes a 'scan': a photo on a white background.

    Used to exercise front-side auto-crop — the photo region is detectable
    against the surrounding white border.
    """
    from PIL import Image

    def _make(path: Path, *, canvas=(600, 600), photo=(300, 200), offset=(120, 150)) -> Path:
        background = Image.new("RGB", canvas, color=(255, 255, 255))
        foreground = Image.new("RGB", photo, color=(40, 90, 160))
        background.paste(foreground, offset)
        path.parent.mkdir(parents=True, exist_ok=True)
        background.save(path, "JPEG", quality=95)
        return path

    return _make


@pytest.fixture
def app_settings(tmp_path):
    """A Settings instance with input/output/config pointed at a temp dir."""
    from albumine.config import Settings

    return Settings(
        input_dir=tmp_path / "input",
        output_dir=tmp_path / "output",
        config_dir=tmp_path / "config",
    )


@pytest.fixture
def session_factory(app_settings):
    """An initialised SQLite database; yields a session factory."""
    from albumine.db import create_db_engine, init_db, make_session_factory

    app_settings.config_dir.mkdir(parents=True, exist_ok=True)
    engine = create_db_engine(app_settings.database_url)
    init_db(engine)
    return make_session_factory(engine)


@pytest.fixture
def fake_provider() -> Callable[..., object]:
    """Return a factory for a fake VisionProvider with controllable behaviour."""
    from albumine.ai.base import (
        AIProviderError,
        BackExtraction,
        ProviderHealth,
        VisionProvider,
    )

    class _FakeProvider(VisionProvider):
        name = "fake"
        model = "fake-vision-1"

        def __init__(self, extraction: BackExtraction | None = None, *, fail: bool = False):
            self._extraction = extraction or BackExtraction(raw_text="leer")
            self._fail = fail
            self.calls = 0

        async def extract_back(self, image, *, mime_type="image/jpeg"):
            self.calls += 1
            if self._fail:
                raise AIProviderError("fake provider is down")
            return self._extraction

        async def health_check(self):
            return ProviderHealth(
                provider=self.name, healthy=not self._fail, detail="fake"
            )

    return _FakeProvider
