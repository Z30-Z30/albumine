"""Tests for front-side (photo) processing."""

import pytest
from PIL import Image

from albumine.ingest.models import PageRef
from albumine.processing.front import (
    FrontProcessingError,
    load_source,
    process_front,
    save_image,
)


def test_load_source_opens_image_file(make_jpeg, tmp_path):
    path = make_jpeg(tmp_path / "photo.jpg")
    image = load_source(PageRef(path))
    assert image.size == (16, 16)


def test_load_source_rejects_non_image(tmp_path):
    bogus = tmp_path / "broken.jpg"
    bogus.write_bytes(b"not really a jpeg")
    with pytest.raises(FrontProcessingError):
        load_source(PageRef(bogus))


def test_process_front_auto_crops_photo_from_scan(make_scan_jpeg, tmp_path):
    # 300x200 photo on a 600x600 white scan.
    scan = make_scan_jpeg(tmp_path / "scan.jpg")
    result = process_front(PageRef(scan), auto_crop=True)

    # The detected region should be roughly the photo, much smaller than the scan.
    width, height = result.size
    assert width < 600 and height < 600
    assert abs(width - 300) <= 25
    assert abs(height - 200) <= 25


def test_process_front_without_auto_crop_keeps_full_frame(make_scan_jpeg, tmp_path):
    scan = make_scan_jpeg(tmp_path / "scan.jpg")
    result = process_front(PageRef(scan), auto_crop=False)
    assert result.size == (600, 600)


def test_process_front_keeps_image_when_nothing_to_crop(make_jpeg, tmp_path):
    # A 16x16 solid image has no detectable photo region.
    path = make_jpeg(tmp_path / "solid.jpg")
    result = process_front(PageRef(path), auto_crop=True)
    assert result.size == (16, 16)


def test_save_image_writes_jpeg(make_jpeg, tmp_path):
    source = load_source(PageRef(make_jpeg(tmp_path / "in.jpg")))
    out = tmp_path / "out.jpg"
    save_image(source, str(out), jpeg_quality=85)

    assert out.exists()
    with Image.open(out) as written:
        assert written.format == "JPEG"
