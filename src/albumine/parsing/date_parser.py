"""Robust parsing of the date scribbled on the back of a photo.

Family-album captions are messy: ``"3.5.85"``, ``"Mai '73"``, ``"Sommer 1962"``,
``"ca. 1970"``, ``"15. März 1980"``. This module turns such free text into a
structured :class:`ParsedDate` with:

* an ISO string at the precision we could actually determine
  (``YYYY-MM-DD`` / ``YYYY-MM`` / ``YYYY``),
* a concrete ``datetime`` for the EXIF ``DateTimeOriginal`` tag — partial dates
  are filled with sensible defaults (year-only → ``01.07.YEAR 12:00``,
  month-known → mid-month, 12:00), and
* a confidence level, so downstream consumers (and the XMP metadata) can record
  how much to trust the value.

The parser never *invents* precision: ``"Sommer 1962"`` stays year-precision,
it is not silently turned into a July date in the ISO string.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

_CURRENT_YEAR = datetime.now().year
_EARLIEST_YEAR = 1800  # photography predates this, but album scans realistically don't


class Confidence(StrEnum):
    """How much to trust a parsed date."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class DatePrecision(StrEnum):
    """The finest date component we could determine from the text."""

    DAY = "day"
    MONTH = "month"
    YEAR = "year"
    NONE = "none"


_CONFIDENCE_RANK = {Confidence.LOW: 1, Confidence.MEDIUM: 2, Confidence.HIGH: 3}


def _cap(confidence: Confidence, ceiling: Confidence) -> Confidence:
    """Return ``confidence`` lowered to at most ``ceiling``."""
    if _CONFIDENCE_RANK[confidence] <= _CONFIDENCE_RANK[ceiling]:
        return confidence
    return ceiling


@dataclass(frozen=True)
class ParsedDate:
    """The structured result of parsing a free-text date.

    Attributes:
        iso: ISO date at the determined precision, or ``None`` if nothing was
            found. One of ``YYYY-MM-DD``, ``YYYY-MM``, ``YYYY``.
        original_text: The input string, verbatim.
        confidence: How much to trust the result.
        precision: The finest component that was actually determined.
        datetime_original: A concrete timestamp suitable for EXIF
            ``DateTimeOriginal`` — partial dates are filled with defaults.
            ``None`` when nothing could be parsed.
    """

    iso: str | None
    original_text: str
    confidence: Confidence
    precision: DatePrecision
    datetime_original: datetime | None

    @classmethod
    def empty(cls, original_text: str) -> ParsedDate:
        """Return a 'nothing parsed' result for the given input."""
        return cls(
            iso=None,
            original_text=original_text,
            confidence=Confidence.LOW,
            precision=DatePrecision.NONE,
            datetime_original=None,
        )


# --- month / season vocabulary ---------------------------------------------

_MONTHS: dict[str, int] = {
    "januar": 1, "jänner": 1, "jaenner": 1, "jan": 1,
    "februar": 2, "feb": 2,
    "märz": 3, "maerz": 3, "mär": 3, "mrz": 3,
    "april": 4, "apr": 4,
    "mai": 5,
    "juni": 6, "jun": 6,
    "juli": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9,
    "oktober": 10, "okt": 10,
    "november": 11, "nov": 11,
    "dezember": 12, "dez": 12,
}
_SEASONS: frozenset[str] = frozenset(
    {"frühling", "fruehling", "frühjahr", "fruehjahr", "sommer", "herbst", "winter"}
)

_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))
_SEASON_ALT = "|".join(sorted(_SEASONS, key=len, reverse=True))

# --- regexes ----------------------------------------------------------------

_APPROX_RE = re.compile(
    r"(\b(ca|circa|zirka|ungefähr|ungefaehr|etwa|um|gegen)\b\.?)|~|±", re.IGNORECASE
)
_ISO_DMY_RE = re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})\b")
_ISO_YM_RE = re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{2})\b")
_DMY_RE = re.compile(
    r"\b(?P<d>\d{1,2})\s*[./-]\s*(?P<m>\d{1,2})\s*[./-]\s*(?P<y>\d{2,4})\b"
)
_DAY_MONTHNAME_RE = re.compile(
    rf"\b(?P<d>\d{{1,2}})\s*\.?\s*(?P<mon>{_MONTH_ALT})\s*'?\s*(?P<y>\d{{2,4}})\b",
    re.IGNORECASE,
)
_MONTHNAME_YEAR_RE = re.compile(
    rf"\b(?P<mon>{_MONTH_ALT})\s*'?\s*(?P<y>\d{{2,4}})\b", re.IGNORECASE
)
_NUMERIC_MY_RE = re.compile(r"\b(?P<m>\d{1,2})\s*[./]\s*(?P<y>\d{4})\b")
_SEASON_YEAR_RE = re.compile(
    rf"\b(?P<s>{_SEASON_ALT})\s*'?\s*(?P<y>\d{{2,4}})\b", re.IGNORECASE
)
_YEAR_4_RE = re.compile(r"\b(?P<y>\d{4})\b")
_YEAR_APOSTROPHE_RE = re.compile(r"'(?P<y>\d{2})\b")
_YEAR_2_RE = re.compile(r"\b(?P<y>\d{2})\b")


def parse_date(text: str) -> ParsedDate:
    """Parse a free-text date string into a :class:`ParsedDate`.

    Returns :meth:`ParsedDate.empty` when no date can be found. Never raises.
    """
    original = text
    cleaned = text.strip()
    if not cleaned:
        return ParsedDate.empty(original)

    approximate = bool(_APPROX_RE.search(cleaned))
    uncertain = "[?]" in cleaned or cleaned.rstrip().endswith("?")

    parsed = _first_match(cleaned)
    if parsed is None:
        return ParsedDate.empty(original)

    year, month, day, precision, base_confidence, two_digit_year = parsed

    confidence = base_confidence
    if two_digit_year:
        confidence = _cap(confidence, Confidence.MEDIUM)
    if uncertain:
        confidence = _cap(confidence, Confidence.MEDIUM)
    if approximate:
        confidence = _cap(confidence, Confidence.LOW)

    return ParsedDate(
        iso=_iso_string(year, month, day, precision),
        original_text=original,
        confidence=confidence,
        precision=precision,
        datetime_original=_datetime_for(year, month, day, precision),
    )


# --- internals --------------------------------------------------------------

# A matcher result: (year, month|None, day|None, precision, base_confidence,
# two_digit_year_flag).
_MatchResult = tuple[int, int | None, int | None, DatePrecision, Confidence, bool]


def _first_match(text: str) -> _MatchResult | None:
    """Try each pattern from most to least specific; return the first hit."""
    for matcher in (
        _try_iso_dmy,
        _try_dmy,
        _try_day_monthname,
        _try_iso_ym,
        _try_monthname_year,
        _try_numeric_my,
        _try_season_year,
        _try_year_only,
    ):
        result = matcher(text)
        if result is not None:
            return result
    return None


def _resolve_year(raw: str) -> tuple[int, bool] | None:
    """Resolve a 2- or 4-digit year token. Returns ``(year, was_two_digit)``."""
    digits = raw.lstrip("'")
    if not digits.isdigit():
        return None
    two_digit = len(digits) <= 2
    year = 1900 + int(digits) if two_digit else int(digits)
    if _EARLIEST_YEAR <= year <= _CURRENT_YEAR:
        return year, two_digit
    return None


def _valid_day(year: int, month: int, day: int) -> bool:
    try:
        datetime(year, month, day)
    except ValueError:
        return False
    return True


def _try_iso_dmy(text: str) -> _MatchResult | None:
    match = _ISO_DMY_RE.search(text)
    if not match:
        return None
    year, month, day = int(match["y"]), int(match["m"]), int(match["d"])
    if not (_EARLIEST_YEAR <= year <= _CURRENT_YEAR) or not _valid_day(year, month, day):
        return None
    return year, month, day, DatePrecision.DAY, Confidence.HIGH, False


def _try_dmy(text: str) -> _MatchResult | None:
    match = _DMY_RE.search(text)
    if not match:
        return None
    resolved = _resolve_year(match["y"])
    if resolved is None:
        return None
    year, two_digit = resolved
    day, month = int(match["d"]), int(match["m"])
    if not (1 <= month <= 12) or not _valid_day(year, month, day):
        return None
    return year, month, day, DatePrecision.DAY, Confidence.HIGH, two_digit


def _try_day_monthname(text: str) -> _MatchResult | None:
    match = _DAY_MONTHNAME_RE.search(text)
    if not match:
        return None
    resolved = _resolve_year(match["y"])
    if resolved is None:
        return None
    year, two_digit = resolved
    month = _MONTHS[match["mon"].lower()]
    day = int(match["d"])
    if not _valid_day(year, month, day):
        return None
    return year, month, day, DatePrecision.DAY, Confidence.HIGH, two_digit


def _try_iso_ym(text: str) -> _MatchResult | None:
    match = _ISO_YM_RE.search(text)
    if not match:
        return None
    year, month = int(match["y"]), int(match["m"])
    if not (_EARLIEST_YEAR <= year <= _CURRENT_YEAR) or not (1 <= month <= 12):
        return None
    return year, month, None, DatePrecision.MONTH, Confidence.HIGH, False


def _try_monthname_year(text: str) -> _MatchResult | None:
    match = _MONTHNAME_YEAR_RE.search(text)
    if not match:
        return None
    resolved = _resolve_year(match["y"])
    if resolved is None:
        return None
    year, two_digit = resolved
    month = _MONTHS[match["mon"].lower()]
    return year, month, None, DatePrecision.MONTH, Confidence.HIGH, two_digit


def _try_numeric_my(text: str) -> _MatchResult | None:
    match = _NUMERIC_MY_RE.search(text)
    if not match:
        return None
    month, year = int(match["m"]), int(match["y"])
    if not (1 <= month <= 12) or not (_EARLIEST_YEAR <= year <= _CURRENT_YEAR):
        return None
    return year, month, None, DatePrecision.MONTH, Confidence.HIGH, False


def _try_season_year(text: str) -> _MatchResult | None:
    match = _SEASON_YEAR_RE.search(text)
    if not match:
        return None
    resolved = _resolve_year(match["y"])
    if resolved is None:
        return None
    year, two_digit = resolved
    # A season pins the year but not the month — keep it year-precision.
    return year, None, None, DatePrecision.YEAR, Confidence.MEDIUM, two_digit


def _try_year_only(text: str) -> _MatchResult | None:
    match = _YEAR_4_RE.search(text)
    if match:
        year = int(match["y"])
        if _EARLIEST_YEAR <= year <= _CURRENT_YEAR:
            return year, None, None, DatePrecision.YEAR, Confidence.HIGH, False

    match = _YEAR_APOSTROPHE_RE.search(text)
    if match:
        resolved = _resolve_year(match["y"])
        if resolved is not None:
            year, _ = resolved
            return year, None, None, DatePrecision.YEAR, Confidence.MEDIUM, True

    match = _YEAR_2_RE.search(text)
    if match:
        resolved = _resolve_year(match["y"])
        if resolved is not None:
            year, _ = resolved
            # A bare two-digit number is the weakest signal there is.
            return year, None, None, DatePrecision.YEAR, Confidence.LOW, True

    return None


def _iso_string(
    year: int, month: int | None, day: int | None, precision: DatePrecision
) -> str:
    if precision is DatePrecision.DAY:
        return f"{year:04d}-{month:02d}-{day:02d}"
    if precision is DatePrecision.MONTH:
        return f"{year:04d}-{month:02d}"
    return f"{year:04d}"


def _datetime_for(
    year: int, month: int | None, day: int | None, precision: DatePrecision
) -> datetime:
    """Fill a partial date with defaults to get a concrete EXIF timestamp."""
    if precision is DatePrecision.DAY:
        return datetime(year, month, day, 12, 0, 0)
    if precision is DatePrecision.MONTH:
        return datetime(year, month, 15, 12, 0, 0)
    # Year-only default, per project spec: 01.07.YEAR 12:00.
    return datetime(year, 7, 1, 12, 0, 0)
