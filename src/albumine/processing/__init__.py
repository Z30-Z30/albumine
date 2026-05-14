"""Processing stage: front-image processing, back OCR, metadata writing."""

from albumine.processing.metadata_writer import (
    EXIFTOOL_CONFIG_PATH,
    ExifToolError,
    PhotoMetadata,
    build_exiftool_args,
    write_metadata,
)

__all__ = [
    "EXIFTOOL_CONFIG_PATH",
    "ExifToolError",
    "PhotoMetadata",
    "build_exiftool_args",
    "write_metadata",
]
