"""Tests for the robust free-text date parser."""

from datetime import datetime

import pytest

from albumine.parsing.date_parser import (
    Confidence,
    DatePrecision,
    parse_date,
)


@pytest.mark.parametrize(
    ("text", "iso", "precision", "confidence"),
    [
        # Full dates.
        ("3.5.1985", "1985-05-03", DatePrecision.DAY, Confidence.HIGH),
        ("15. März 1980", "1980-03-15", DatePrecision.DAY, Confidence.HIGH),
        ("1962-07-01", "1962-07-01", DatePrecision.DAY, Confidence.HIGH),
        # Two-digit year drops confidence to medium.
        ("3.5.85", "1985-05-03", DatePrecision.DAY, Confidence.MEDIUM),
        # Month + year.
        ("Mai 1973", "1973-05", DatePrecision.MONTH, Confidence.HIGH),
        ("Mai '73", "1973-05", DatePrecision.MONTH, Confidence.MEDIUM),
        ("1962-07", "1962-07", DatePrecision.MONTH, Confidence.HIGH),
        ("12.1985", "1985-12", DatePrecision.MONTH, Confidence.HIGH),
        # Season + year stays year-precision, medium confidence.
        ("Sommer 1962", "1962", DatePrecision.YEAR, Confidence.MEDIUM),
        ("Frühling 65", "1965", DatePrecision.YEAR, Confidence.MEDIUM),
        # Year only.
        ("1970", "1970", DatePrecision.YEAR, Confidence.HIGH),
        ("Hochzeit 1959", "1959", DatePrecision.YEAR, Confidence.HIGH),
        # Approximation markers force low confidence.
        ("ca. 1970", "1970", DatePrecision.YEAR, Confidence.LOW),
        ("um 1955", "1955", DatePrecision.YEAR, Confidence.LOW),
        ("~1948", "1948", DatePrecision.YEAR, Confidence.LOW),
    ],
)
def test_parse_date_examples(text, iso, precision, confidence):
    result = parse_date(text)
    assert result.iso == iso
    assert result.precision is precision
    assert result.confidence is confidence
    assert result.original_text == text


@pytest.mark.parametrize("text", ["", "   ", "keine Ahnung", "Hochzeit von Anna"])
def test_parse_date_no_date_found(text):
    result = parse_date(text)
    assert result.iso is None
    assert result.precision is DatePrecision.NONE
    assert result.datetime_original is None


def test_uncertain_marker_caps_confidence_at_medium():
    result = parse_date("Mai 1973 [?]")
    assert result.iso == "1973-05"
    assert result.confidence is Confidence.MEDIUM


def test_datetime_original_day_precision():
    result = parse_date("3.5.1985")
    assert result.datetime_original == datetime(1985, 5, 3, 12, 0, 0)


def test_datetime_original_month_precision_uses_mid_month():
    result = parse_date("Mai 1973")
    assert result.datetime_original == datetime(1973, 5, 15, 12, 0, 0)


def test_datetime_original_year_precision_uses_july_first():
    # Project spec: year-only defaults to 01.07.YEAR 12:00.
    result = parse_date("Sommer 1962")
    assert result.datetime_original == datetime(1962, 7, 1, 12, 0, 0)


def test_invalid_day_month_falls_back_to_year():
    # 32.13.1985 is not a valid D.M.Y — but the year is still recoverable.
    result = parse_date("32.13.1985")
    assert result.iso == "1985"
    assert result.precision is DatePrecision.YEAR


def test_implausible_year_is_rejected():
    result = parse_date("3024")
    assert result.iso is None


def test_two_digit_year_resolves_to_20th_century():
    # Family-album scans: a bare two-digit year is assumed to be 19xx.
    result = parse_date("Juli 88")
    assert result.iso == "1988-07"
