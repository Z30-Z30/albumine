"""Tests for the ExifTool metadata writer.

The argument builder is tested as a pure function (no ExifTool needed). The
end-to-end ``write_metadata`` test runs only when ``exiftool`` is on PATH.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from albumine.parsing.date_parser import parse_date
from albumine.processing.metadata_writer import (
    EXIFTOOL_CONFIG_PATH,
    PhotoMetadata,
    build_exiftool_args,
    write_metadata,
)

_HAS_EXIFTOOL = shutil.which("exiftool") is not None


def _full_metadata() -> PhotoMetadata:
    return PhotoMetadata(
        raw_text="Hochzeit von Anna und Hans, Mai 1973",
        description="Hochzeitsfoto, aufgenommen in Zürich.",
        location="Zürich",
        people=["Anna", "Hans"],
        event="Hochzeit",
        date=parse_date("Mai 1973"),
        ai_provider="ollama",
        ai_model="llava",
        source_files=["/input/foto_001a.jpg", "/input/foto_001b.jpg"],
    )


# --- argument builder (pure) -----------------------------------------------


def test_build_args_includes_config_and_target_last():
    args = build_exiftool_args(_full_metadata(), Path("/output/photo.jpg"))
    assert args[0] == "exiftool"
    assert "-config" in args
    assert str(EXIFTOOL_CONFIG_PATH) in args
    assert "-overwrite_original" in args
    assert args[-1] == "/output/photo.jpg"


def test_build_args_writes_date_tags():
    args = build_exiftool_args(_full_metadata(), Path("/output/photo.jpg"))
    assert "-EXIF:DateTimeOriginal=1973:05:15 12:00:00" in args
    assert "-EXIF:CreateDate=1973:05:15 12:00:00" in args
    assert "-XMP-albumine:DateConfidence=high" in args
    assert "-XMP-albumine:DatePrecision=month" in args


def test_build_args_writes_caption_and_description():
    args = build_exiftool_args(_full_metadata(), Path("/output/photo.jpg"))
    assert "-IPTC:Caption-Abstract=Hochzeit von Anna und Hans, Mai 1973" in args
    assert "-XMP-dc:Description=Hochzeitsfoto, aufgenommen in Zürich." in args


def test_build_args_collects_keywords_from_people_event_location():
    args = build_exiftool_args(_full_metadata(), Path("/output/photo.jpg"))
    keywords = {a.split("=", 1)[1] for a in args if a.startswith("-IPTC:Keywords=")}
    assert keywords == {"Anna", "Hans", "Hochzeit", "Zürich"}


def test_build_args_writes_provenance_namespace():
    args = build_exiftool_args(_full_metadata(), Path("/output/photo.jpg"))
    assert "-XMP-albumine:AiProvider=ollama" in args
    assert "-XMP-albumine:AiModel=llava" in args
    assert any(a.startswith("-XMP-albumine:ProcessingVersion=") for a in args)
    sources = {a.split("=", 1)[1] for a in args if a.startswith("-XMP-albumine:SourceFiles=")}
    assert sources == {"/input/foto_001a.jpg", "/input/foto_001b.jpg"}


def test_build_args_skips_empty_values():
    args = build_exiftool_args(
        PhotoMetadata(raw_text="  ", description=None, people=[" ", ""]),
        Path("/output/photo.jpg"),
    )
    assert not any(a.startswith("-IPTC:Caption-Abstract=") for a in args)
    assert not any(a.startswith("-XMP-dc:Description=") for a in args)
    assert not any(a.startswith("-IPTC:Keywords=") for a in args)
    # No date supplied -> no date tags.
    assert not any(a.startswith("-EXIF:DateTimeOriginal=") for a in args)


def test_build_args_deduplicates_keywords():
    metadata = PhotoMetadata(people=["Anna", "Anna"], event="Anna")
    args = build_exiftool_args(metadata, Path("/output/photo.jpg"))
    keywords = [a for a in args if a.startswith("-IPTC:Keywords=")]
    assert keywords == ["-IPTC:Keywords=Anna"]


# --- end-to-end (requires exiftool) ----------------------------------------


@pytest.mark.skipif(not _HAS_EXIFTOOL, reason="exiftool not installed")
def test_write_metadata_roundtrip(make_jpeg, tmp_path):
    image = make_jpeg(tmp_path / "photo.jpg")
    metadata = _full_metadata()

    write_metadata(image, metadata)

    raw = subprocess.run(
        ["exiftool", "-config", str(EXIFTOOL_CONFIG_PATH), "-json", "-G1", str(image)],
        capture_output=True,
        text=True,
        check=True,
    )
    tags = json.loads(raw.stdout)[0]
    assert tags["IPTC:Caption-Abstract"] == metadata.raw_text
    assert tags["ExifIFD:DateTimeOriginal"] == "1973:05:15 12:00:00"
    assert tags["XMP-albumine:DateConfidence"] == "high"
    assert tags["XMP-albumine:AiProvider"] == "ollama"
    assert set(tags["IPTC:Keywords"]) == {"Anna", "Hans", "Hochzeit", "Zürich"}


@pytest.mark.skipif(not _HAS_EXIFTOOL, reason="exiftool not installed")
def test_write_metadata_sidecar(make_jpeg, tmp_path):
    image = make_jpeg(tmp_path / "photo.jpg")
    write_metadata(image, _full_metadata(), sidecar=True)
    assert (tmp_path / "photo.jpg.xmp").exists()
