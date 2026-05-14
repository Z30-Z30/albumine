"""Ingest stage: watch-folder monitoring, pair detection, PDF splitting."""

from albumine.ingest.models import (
    DetectionMethod,
    PageRef,
    ScanPair,
)
from albumine.ingest.pair_detector import detect_pairs, scan_directory
from albumine.ingest.pdf_splitter import (
    PdfReadError,
    count_pages,
    extract_page,
    split_pdf,
)
from albumine.ingest.watcher import FolderWatcher

__all__ = [
    "DetectionMethod",
    "FolderWatcher",
    "PageRef",
    "PdfReadError",
    "ScanPair",
    "count_pages",
    "detect_pairs",
    "extract_page",
    "scan_directory",
    "split_pdf",
]
