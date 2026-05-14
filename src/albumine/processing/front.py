"""Front-side (photo) processing.

Baseline pipeline — the ``none`` enhancement level from the spec: load the
source (image file or rasterised PDF page), fix orientation, and detect/extract
the photo from the surrounding scanner background (combined auto-crop + deskew
via a single perspective warp).

Colour correction, denoising, upscaling and face restoration are higher
enhancement levels and live in ``enhance.py`` (a later phase).
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageOps

from albumine.ingest.models import PageRef
from albumine.logging import get_logger

_log = get_logger(__name__)

_PDF_RENDER_DPI = 300

# A detected photo region is only used if it is a sensible fraction of the
# scan — too close to the full frame means "nothing to crop", too small means
# the detection probably latched onto noise.
_MIN_REGION_AREA_RATIO = 0.15
_MAX_REGION_AREA_RATIO = 0.98
_MIN_REGION_SIDE_PX = 50


class FrontProcessingError(RuntimeError):
    """Raised when the front source cannot be loaded or processed."""


def load_source(page_ref: PageRef) -> Image.Image:
    """Load a :class:`PageRef` into a PIL image.

    Image files are opened directly; PDF pages are rasterised via ``pdf2image``
    (which needs the poppler system package).

    Raises:
        FrontProcessingError: If the source cannot be loaded.
    """
    path = page_ref.path
    if page_ref.page_index is None:
        try:
            return Image.open(path)
        except OSError as exc:
            raise FrontProcessingError(f"could not open image {path}: {exc}") from exc

    try:
        from pdf2image import convert_from_path
    except ImportError as exc:  # pragma: no cover - dependency always present in prod
        raise FrontProcessingError("pdf2image is not available") from exc

    page_number = page_ref.page_index + 1
    try:
        pages = convert_from_path(
            str(path), dpi=_PDF_RENDER_DPI, first_page=page_number, last_page=page_number
        )
    except Exception as exc:  # noqa: BLE001 - pdf2image raises a broad set of errors
        raise FrontProcessingError(
            f"could not rasterise page {page_number} of {path}: {exc}"
        ) from exc
    if not pages:
        raise FrontProcessingError(f"PDF {path} has no page {page_number}")
    return pages[0]


def process_front(
    page_ref: PageRef, *, auto_crop: bool = True
) -> Image.Image:
    """Load and clean up the front image.

    Steps: load source -> apply EXIF orientation -> (optional) detect and
    extract the photo region from the scan background.

    Args:
        page_ref: The front source.
        auto_crop: Whether to attempt photo-region detection + deskew.

    Returns:
        The processed image in RGB mode.
    """
    image = load_source(page_ref)
    image = ImageOps.exif_transpose(image)  # honour camera/scanner rotation
    image = image.convert("RGB")

    if not auto_crop:
        return image

    extracted = _extract_photo_region(image)
    if extracted is not None:
        _log.info("front.cropped", source=str(page_ref))
        return extracted
    _log.info("front.crop_skipped", source=str(page_ref))
    return image


def save_image(image: Image.Image, target_path: str, *, jpeg_quality: int = 90) -> None:
    """Save a processed image as JPEG (RGB)."""
    image.convert("RGB").save(target_path, "JPEG", quality=jpeg_quality)


# --- photo-region detection -------------------------------------------------


def _extract_photo_region(image: Image.Image) -> Image.Image | None:
    """Detect the photo within the scan and return it deskewed + cropped.

    Returns ``None`` when no sensible region is found (caller keeps the original).
    """
    rgb = np.asarray(image)
    height, width = rgb.shape[:2]
    full_area = float(width * height)

    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    edges = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=2)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    largest = max(contours, key=cv2.contourArea)
    rect = cv2.minAreaRect(largest)
    (_, _), (rect_w, rect_h), _ = rect
    if rect_w < _MIN_REGION_SIDE_PX or rect_h < _MIN_REGION_SIDE_PX:
        return None

    area_ratio = (rect_w * rect_h) / full_area
    if not (_MIN_REGION_AREA_RATIO <= area_ratio <= _MAX_REGION_AREA_RATIO):
        return None

    warped = _warp_to_rect(rgb, rect)
    return Image.fromarray(warped)


def _warp_to_rect(rgb: np.ndarray, rect: tuple) -> np.ndarray:
    """Perspective-warp the (possibly rotated) ``rect`` to an upright crop."""
    box = cv2.boxPoints(rect)
    ordered = _order_points(box)
    (top_left, top_right, bottom_right, bottom_left) = ordered

    width = int(round(max(
        np.linalg.norm(top_right - top_left),
        np.linalg.norm(bottom_right - bottom_left),
    )))
    height = int(round(max(
        np.linalg.norm(bottom_left - top_left),
        np.linalg.norm(bottom_right - top_right),
    )))

    destination = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype="float32",
    )
    transform = cv2.getPerspectiveTransform(ordered, destination)
    return cv2.warpPerspective(rgb, transform, (width, height))


def _order_points(points: np.ndarray) -> np.ndarray:
    """Order 4 points as top-left, top-right, bottom-right, bottom-left."""
    points = points.astype("float32")
    summed = points.sum(axis=1)
    diff = np.diff(points, axis=1).ravel()
    return np.array(
        [
            points[np.argmin(summed)],  # top-left: smallest x+y
            points[np.argmin(diff)],    # top-right: smallest y-x
            points[np.argmax(summed)],  # bottom-right: largest x+y
            points[np.argmax(diff)],    # bottom-left: largest y-x
        ],
        dtype="float32",
    )
