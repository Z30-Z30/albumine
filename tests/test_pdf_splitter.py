"""Tests for PDF page counting and splitting."""

import pytest
from pypdf import PdfReader

from albumine.ingest.pdf_splitter import (
    PdfReadError,
    count_pages,
    extract_page,
    split_pdf,
)


def test_count_pages(make_pdf, tmp_path):
    pdf = make_pdf(tmp_path / "album.pdf", pages=6)
    assert count_pages(pdf) == 6


def test_count_pages_rejects_non_pdf(tmp_path):
    bogus = tmp_path / "not-a.pdf"
    bogus.write_bytes(b"this is not a pdf")
    with pytest.raises(PdfReadError):
        count_pages(bogus)


def test_split_pdf_writes_one_file_per_page(make_pdf, tmp_path):
    pdf = make_pdf(tmp_path / "album.pdf", pages=4)
    out_dir = tmp_path / "split"

    parts = split_pdf(pdf, out_dir)

    assert len(parts) == 4
    assert [p.name for p in parts] == [
        "album_p001.pdf",
        "album_p002.pdf",
        "album_p003.pdf",
        "album_p004.pdf",
    ]
    for part in parts:
        assert part.exists()
        assert len(PdfReader(str(part)).pages) == 1


def test_extract_page(make_pdf, tmp_path):
    pdf = make_pdf(tmp_path / "album.pdf", pages=3)
    out = tmp_path / "page2.pdf"

    result = extract_page(pdf, page_index=1, out_path=out)

    assert result == out
    assert out.exists()
    assert len(PdfReader(str(out)).pages) == 1


def test_extract_page_out_of_range(make_pdf, tmp_path):
    pdf = make_pdf(tmp_path / "album.pdf", pages=2)
    with pytest.raises(IndexError):
        extract_page(pdf, page_index=5, out_path=tmp_path / "x.pdf")
