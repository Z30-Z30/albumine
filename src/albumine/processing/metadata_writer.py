"""Write extracted metadata into image files via ExifTool.

ExifTool is used (over ``piexif``) because it writes EXIF, IPTC *and* XMP across
many formats — including a custom XMP namespace for AlbuMine's own provenance
data (see ``exiftool_albumine.config``).

The module is split into a pure argument builder (:func:`build_exiftool_args`,
fully unit-testable without ExifTool installed) and the subprocess call
(:func:`write_metadata`).
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from albumine import __version__
from albumine.logging import get_logger
from albumine.parsing.date_parser import ParsedDate

_log = get_logger(__name__)

#: Path to the bundled ExifTool config that declares the ``albumine`` XMP namespace.
EXIFTOOL_CONFIG_PATH = Path(__file__).parent / "exiftool_albumine.config"

#: EXIF timestamp format, e.g. ``2026:05:14 12:00:00``.
_EXIF_DT_FORMAT = "%Y:%m:%d %H:%M:%S"


class ExifToolError(RuntimeError):
    """Raised when ExifTool is unavailable or exits with an error."""


@dataclass
class PhotoMetadata:
    """Everything AlbuMine knows about a photo, ready to be written to the file.

    Phase 4's AI extraction result maps into this structure; for now it is
    populated by hand or in tests.
    """

    # --- descriptive content -------------------------------------------------
    raw_text: str | None = None       # verbatim back-of-photo transcription
    description: str | None = None    # human-readable structured description
    location: str | None = None
    people: list[str] = field(default_factory=list)
    event: str | None = None
    notes: str | None = None

    # --- date ----------------------------------------------------------------
    date: ParsedDate | None = None

    # --- AlbuMine provenance (custom XMP namespace) --------------------------
    ai_provider: str | None = None
    ai_model: str | None = None
    enhancement_level: str | None = None
    processing_version: str = __version__
    source_files: list[str] = field(default_factory=list)

    def keywords(self) -> list[str]:
        """Collect people, event and location into a deduplicated keyword list."""
        candidates = [*self.people, self.event, self.location]
        seen: dict[str, None] = {}
        for candidate in candidates:
            if candidate and candidate.strip():
                seen.setdefault(candidate.strip(), None)
        return list(seen)


def build_exiftool_args(
    metadata: PhotoMetadata,
    target_path: Path,
    *,
    config_path: Path = EXIFTOOL_CONFIG_PATH,
) -> list[str]:
    """Build the ExifTool command line for writing ``metadata`` to ``target_path``.

    Pure function — does not touch the filesystem or run anything. Values that
    are empty or whitespace-only are skipped.
    """
    args: list[str] = [
        "exiftool",
        "-config",
        str(config_path),
        "-overwrite_original",
        # Tell ExifTool the arguments are UTF-8, and record CodedCharacterSet in
        # the file so any reader knows the IPTC block is UTF-8 (otherwise IPTC
        # defaults to Latin-1 and umlauts come back mojibake).
        "-charset",
        "iptc=UTF8",
        "-codedcharacterset=utf8",
    ]

    date = metadata.date
    if date is not None and date.datetime_original is not None:
        stamp = date.datetime_original.strftime(_EXIF_DT_FORMAT)
        args.append(f"-EXIF:DateTimeOriginal={stamp}")
        args.append(f"-EXIF:CreateDate={stamp}")

    if _has_text(metadata.raw_text):
        args.append(f"-IPTC:Caption-Abstract={metadata.raw_text.strip()}")

    if _has_text(metadata.description):
        args.append(f"-XMP-dc:Description={metadata.description.strip()}")

    for keyword in metadata.keywords():
        args.append(f"-IPTC:Keywords={keyword}")
        args.append(f"-XMP-dc:Subject={keyword}")

    # AlbuMine custom XMP namespace.
    if date is not None:
        args.append(f"-XMP-albumine:DateConfidence={date.confidence}")
        args.append(f"-XMP-albumine:DatePrecision={date.precision}")
        if _has_text(date.original_text):
            args.append(f"-XMP-albumine:DateOriginalText={date.original_text.strip()}")
    if _has_text(metadata.ai_provider):
        args.append(f"-XMP-albumine:AiProvider={metadata.ai_provider.strip()}")
    if _has_text(metadata.ai_model):
        args.append(f"-XMP-albumine:AiModel={metadata.ai_model.strip()}")
    if _has_text(metadata.enhancement_level):
        args.append(f"-XMP-albumine:EnhancementLevel={metadata.enhancement_level.strip()}")
    args.append(f"-XMP-albumine:ProcessingVersion={metadata.processing_version}")
    for source in metadata.source_files:
        if _has_text(source):
            args.append(f"-XMP-albumine:SourceFiles={source.strip()}")

    args.append(str(target_path))
    return args


def write_metadata(
    image_path: Path,
    metadata: PhotoMetadata,
    *,
    sidecar: bool = False,
) -> None:
    """Write ``metadata`` into ``image_path`` using ExifTool.

    Args:
        image_path: The image file to tag (modified in place).
        metadata: The metadata to write.
        sidecar: If true, additionally write an ``<image>.xmp`` sidecar file
            holding the same data.

    Raises:
        ExifToolError: If ExifTool is not installed or exits non-zero.
    """
    _run(build_exiftool_args(metadata, image_path), image_path)
    if sidecar:
        sidecar_path = image_path.with_name(image_path.name + ".xmp")
        _run(build_exiftool_args(metadata, sidecar_path), sidecar_path)
    _log.info(
        "metadata.written",
        image=str(image_path),
        sidecar=sidecar,
        keywords=len(metadata.keywords()),
    )


def _run(args: list[str], target_path: Path) -> None:
    if shutil.which("exiftool") is None:
        raise ExifToolError("exiftool not found on PATH")
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise ExifToolError(f"exiftool failed for {target_path}: {message}")


def _has_text(value: str | None) -> bool:
    return value is not None and bool(value.strip())
