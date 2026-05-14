"""PDF splitting at the page level.

This module works purely on the PDF structure (via ``pypdf``) — it counts and
extracts pages without rasterising them. Converting a page to a PIL image
(poppler / ``pdf2image``) is a processing concern handled in a later phase.
"""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PyPdfError

from albumine.logging import get_logger

_log = get_logger(__name__)

# Errors that mean "this file is not a usable PDF".
_PDF_PARSE_ERRORS = (PyPdfError, OSError, ValueError)


class PdfReadError(RuntimeError):
    """Raised when a PDF cannot be parsed."""


def count_pages(pdf_path: Path) -> int:
    """Return the number of pages in a PDF.

    Raises:
        PdfReadError: If the file cannot be parsed as a PDF.
    """
    try:
        return len(PdfReader(str(pdf_path)).pages)
    except _PDF_PARSE_ERRORS as exc:
        raise PdfReadError(f"could not read PDF {pdf_path}: {exc}") from exc


def split_pdf(pdf_path: Path, out_dir: Path) -> list[Path]:
    """Split a PDF into one single-page PDF file per page.

    Args:
        pdf_path: Source PDF.
        out_dir: Directory to write the per-page PDFs into (created if missing).

    Returns:
        The written single-page PDF paths, in page order.

    Raises:
        PdfReadError: If the source file cannot be parsed.
    """
    try:
        reader = PdfReader(str(pdf_path))
    except _PDF_PARSE_ERRORS as exc:
        raise PdfReadError(f"could not read PDF {pdf_path}: {exc}") from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for index, page in enumerate(reader.pages):
        writer = PdfWriter()
        writer.add_page(page)
        out_path = out_dir / f"{pdf_path.stem}_p{index + 1:03d}.pdf"
        with out_path.open("wb") as handle:
            writer.write(handle)
        outputs.append(out_path)

    _log.info("pdf.split", source=str(pdf_path), pages=len(outputs))
    return outputs


def extract_page(pdf_path: Path, page_index: int, out_path: Path) -> Path:
    """Write a single page of a PDF to its own single-page PDF file.

    Args:
        pdf_path: Source PDF.
        page_index: 0-based page index to extract.
        out_path: Destination file (parent directory created if missing).

    Returns:
        ``out_path``.

    Raises:
        PdfReadError: If the source file cannot be parsed.
        IndexError: If ``page_index`` is out of range.
    """
    try:
        reader = PdfReader(str(pdf_path))
    except _PDF_PARSE_ERRORS as exc:
        raise PdfReadError(f"could not read PDF {pdf_path}: {exc}") from exc

    writer = PdfWriter()
    writer.add_page(reader.pages[page_index])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as handle:
        writer.write(handle)
    return out_path
