"""Tests for content hashing and stable pair identifiers."""

from albumine.ingest.hashing import file_sha256, make_pair_id
from albumine.ingest.models import PageRef


def test_file_sha256_is_content_based(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"identical")
    b.write_bytes(b"identical")
    c = tmp_path / "c.bin"
    c.write_bytes(b"different")

    assert file_sha256(a) == file_sha256(b)
    assert file_sha256(a) != file_sha256(c)


def test_pair_id_is_deterministic(make_image, tmp_path):
    front = make_image(tmp_path / "f.jpg", b"front-bytes")
    back = make_image(tmp_path / "b.jpg", b"back-bytes")

    id1 = make_pair_id(PageRef(front), PageRef(back), (front, back))
    id2 = make_pair_id(PageRef(front), PageRef(back), (front, back))
    assert id1 == id2
    assert len(id1) == 16


def test_pair_id_distinguishes_pdf_pages(make_pdf, tmp_path):
    """Two pairs from the same PDF file must still get distinct ids."""
    pdf = make_pdf(tmp_path / "album.pdf", pages=4)

    pair_a = make_pair_id(PageRef(pdf, 0), PageRef(pdf, 1), (pdf,))
    pair_b = make_pair_id(PageRef(pdf, 2), PageRef(pdf, 3), (pdf,))
    assert pair_a != pair_b


def test_pair_id_changes_with_content(make_image, tmp_path):
    front = make_image(tmp_path / "f.jpg", b"original")
    id_before = make_pair_id(PageRef(front), None, (front,))

    front.write_bytes(b"edited")
    id_after = make_pair_id(PageRef(front), None, (front,))
    assert id_before != id_after
