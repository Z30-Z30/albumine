"""Tests for the pair-detection heuristics."""

from albumine.ingest.models import DetectionMethod
from albumine.ingest.pair_detector import detect_pairs, scan_directory

# --- PDF cases --------------------------------------------------------------


def test_two_page_pdf_is_a_duplex_pair(make_pdf, tmp_path):
    pdf = make_pdf(tmp_path / "scan.pdf", pages=2)

    pairs = detect_pairs([pdf])

    assert len(pairs) == 1
    pair = pairs[0]
    assert pair.method is DetectionMethod.PDF_DUPLEX
    assert pair.front.path == pdf and pair.front.page_index == 0
    assert pair.back is not None and pair.back.page_index == 1
    assert pair.needs_review is False


def test_multi_page_pdf_splits_into_alternating_pairs(make_pdf, tmp_path):
    pdf = make_pdf(tmp_path / "album.pdf", pages=6)

    pairs = detect_pairs([pdf])

    assert len(pairs) == 3
    assert all(p.method is DetectionMethod.PDF_MULTI for p in pairs)
    assert [(p.front.page_index, p.back.page_index) for p in pairs] == [
        (0, 1),
        (2, 3),
        (4, 5),
    ]
    assert not any(p.needs_review for p in pairs)


def test_single_page_pdf_is_front_only(make_pdf, tmp_path):
    pdf = make_pdf(tmp_path / "lonely.pdf", pages=1)

    pairs = detect_pairs([pdf])

    assert len(pairs) == 1
    assert pairs[0].method is DetectionMethod.SINGLE_PDF
    assert pairs[0].back is None
    assert pairs[0].needs_review is False


def test_odd_multipage_pdf_needs_review(make_pdf, tmp_path):
    pdf = make_pdf(tmp_path / "odd.pdf", pages=5)

    pairs = detect_pairs([pdf])

    assert len(pairs) == 1
    assert pairs[0].method is DetectionMethod.AMBIGUOUS
    assert pairs[0].needs_review is True
    assert pairs[0].note is not None


def test_unreadable_pdf_needs_review(tmp_path):
    bogus = tmp_path / "broken.pdf"
    bogus.write_bytes(b"definitely not a pdf")

    pairs = detect_pairs([bogus])

    assert len(pairs) == 1
    assert pairs[0].method is DetectionMethod.AMBIGUOUS
    assert pairs[0].needs_review is True


# --- Image-pair cases -------------------------------------------------------


def test_image_pair_via_ab_suffix(make_image, tmp_path):
    front = make_image(tmp_path / "foto_001a.jpg", b"front")
    back = make_image(tmp_path / "foto_001b.jpg", b"back")

    pairs = detect_pairs([front, back])

    assert len(pairs) == 1
    pair = pairs[0]
    assert pair.method is DetectionMethod.IMAGE_PAIR
    assert pair.front.path == front
    assert pair.back is not None and pair.back.path == back
    assert pair.needs_review is False


def test_image_pair_via_front_back_words(make_image, tmp_path):
    front = make_image(tmp_path / "hochzeit_front.jpg", b"f")
    back = make_image(tmp_path / "hochzeit_back.jpg", b"b")

    pairs = detect_pairs([front, back])

    assert len(pairs) == 1
    assert pairs[0].method is DetectionMethod.IMAGE_PAIR
    assert pairs[0].front.path == front
    assert pairs[0].back.path == back


def test_mixed_marker_conventions_still_pair(make_image, tmp_path):
    """A digit-suffix front and a word-suffix back sharing a base still pair."""
    front = make_image(tmp_path / "foto_001a.jpg", b"f")
    back = make_image(tmp_path / "foto_001_back.jpg", b"b")

    pairs = detect_pairs([front, back])

    assert len(pairs) == 1
    assert pairs[0].method is DetectionMethod.IMAGE_PAIR


def test_lone_image_without_marker_is_front_only(make_image, tmp_path):
    image = make_image(tmp_path / "vacation.jpg", b"x")

    pairs = detect_pairs([image])

    assert len(pairs) == 1
    assert pairs[0].method is DetectionMethod.SINGLE_IMAGE
    assert pairs[0].back is None
    assert pairs[0].needs_review is False


def test_name_ending_in_letter_is_not_mistaken_for_a_pair(make_image, tmp_path):
    """'banana.jpg' must not be parsed as base 'banan' + side 'a'."""
    image = make_image(tmp_path / "banana.jpg", b"x")

    pairs = detect_pairs([image])

    assert len(pairs) == 1
    assert pairs[0].method is DetectionMethod.SINGLE_IMAGE


def test_orphan_marked_image_needs_review(make_image, tmp_path):
    orphan = make_image(tmp_path / "foto_002a.jpg", b"x")

    pairs = detect_pairs([orphan])

    assert len(pairs) == 1
    assert pairs[0].method is DetectionMethod.AMBIGUOUS
    assert pairs[0].needs_review is True


def test_conflicting_markers_surface_every_file(make_image, tmp_path):
    a = make_image(tmp_path / "foto_003a.jpg", b"1")
    b = make_image(tmp_path / "foto_003_front.jpg", b"2")  # second 'front'

    pairs = detect_pairs([a, b])

    assert len(pairs) == 2
    assert all(p.method is DetectionMethod.AMBIGUOUS for p in pairs)
    assert all(p.needs_review for p in pairs)


# --- Duplex-scanner suffix cases (BASE.jpg + BASE_001.jpg) -------------------


def test_scanner_suffix_pair(make_image, tmp_path):
    """Document scanners write the front as BASE.jpg and the back as BASE_001.jpg."""
    front = make_image(tmp_path / "13102024111936.jpg", b"front")
    back = make_image(tmp_path / "13102024111936_001.jpg", b"back")

    pairs = detect_pairs([front, back])

    assert len(pairs) == 1
    pair = pairs[0]
    assert pair.method is DetectionMethod.IMAGE_PAIR
    assert pair.front.path == front
    assert pair.back is not None and pair.back.path == back
    assert pair.needs_review is False


def test_lone_numeric_suffix_stays_single(make_image, tmp_path):
    """A lone X_001 may be plain sequence numbering — no pairing, no review."""
    image = make_image(tmp_path / "12102024112035_001.jpg", b"x")

    pairs = detect_pairs([image])

    assert len(pairs) == 1
    assert pairs[0].method is DetectionMethod.SINGLE_IMAGE
    assert pairs[0].needs_review is False


def test_sequence_numbering_without_base_stays_single(make_image, tmp_path):
    """scan_001/scan_002 without scan.jpg is a sequence, not duplex pairs."""
    one = make_image(tmp_path / "scan_001.jpg", b"1")
    two = make_image(tmp_path / "scan_002.jpg", b"2")

    pairs = detect_pairs([one, two])

    assert len(pairs) == 2
    assert all(p.method is DetectionMethod.SINGLE_IMAGE for p in pairs)


def test_multiple_numeric_siblings_need_review(make_image, tmp_path):
    """BASE + _001 + _002: sheet assignment is unclear — surface all files."""
    base = make_image(tmp_path / "urlaub.jpg", b"0")
    one = make_image(tmp_path / "urlaub_001.jpg", b"1")
    two = make_image(tmp_path / "urlaub_002.jpg", b"2")

    pairs = detect_pairs([base, one, two])

    assert len(pairs) == 3
    assert all(p.method is DetectionMethod.AMBIGUOUS for p in pairs)
    assert all(p.needs_review for p in pairs)


# --- Misc -------------------------------------------------------------------


def test_non_media_files_are_ignored(make_image, tmp_path):
    image = make_image(tmp_path / "photo.jpg", b"x")
    make_image(tmp_path / "notes.txt", b"ignore me")
    make_image(tmp_path / "thumbs.db", b"ignore me too")

    pairs = detect_pairs(list(tmp_path.iterdir()))

    assert len(pairs) == 1
    assert pairs[0].front.path == image


def test_detection_is_deterministic_and_idempotent(make_pdf, make_image, tmp_path):
    pdf = make_pdf(tmp_path / "album.pdf", pages=2)
    front = make_image(tmp_path / "foto_001a.jpg", b"f")
    back = make_image(tmp_path / "foto_001b.jpg", b"b")
    inputs = [back, pdf, front]  # deliberately unsorted

    first = detect_pairs(inputs)
    second = detect_pairs(inputs)

    assert [p.pair_id for p in first] == [p.pair_id for p in second]
    assert [p.method for p in first] == [p.method for p in second]


def test_scan_directory(make_pdf, tmp_path):
    make_pdf(tmp_path / "scan.pdf", pages=2)

    pairs = scan_directory(tmp_path)

    assert len(pairs) == 1
    assert pairs[0].method is DetectionMethod.PDF_DUPLEX


def test_scan_directory_missing_folder_returns_empty(tmp_path):
    assert scan_directory(tmp_path / "does-not-exist") == []
