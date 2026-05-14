"""Image-enhancement pipeline — the ``basic`` / ``enhance`` / ``restore`` levels.

Levels build on each other:

* ``none``    — nothing (crop/deskew already happened in front processing).
* ``basic``   — white-balance + contrast correction + light denoise, all with
  Pillow/OpenCV. Always available, no extra tooling.
* ``enhance`` — ``basic`` + Real-ESRGAN upscaling.
* ``restore`` — ``enhance`` + GFPGAN face restoration.

Real-ESRGAN and GFPGAN are heavy ML tools, so — per the project spec — they are
invoked as **external CLI subprocesses** rather than pulled in as Python
dependencies. Their binary paths are configured via settings; if a tool is not
configured (or fails), enhancement **degrades gracefully** to the highest level
that *did* succeed, and the actually-applied level is returned to the caller.

The CLI contract for both tools is ``<bin> -i <input> -o <output> [extra args]``.
"""

from __future__ import annotations

import shlex
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from albumine.config import EnhancementLevel, Settings
from albumine.logging import get_logger

_log = get_logger(__name__)

_SUBPROCESS_TIMEOUT = 600  # ML upscaling can be slow, especially on CPU


def apply_enhancement(
    image: Image.Image,
    level: EnhancementLevel,
    *,
    settings: Settings,
) -> tuple[Image.Image, EnhancementLevel]:
    """Apply image enhancement up to ``level``.

    Returns the enhanced image together with the level that was *actually*
    applied — which may be lower than requested if an external tool is not
    configured or fails (graceful degradation).
    """
    if level is EnhancementLevel.NONE:
        return image, EnhancementLevel.NONE

    result = _basic_enhance(image)
    applied = EnhancementLevel.BASIC

    if level in (EnhancementLevel.ENHANCE, EnhancementLevel.RESTORE):
        upscaled = _run_external_tool(
            "realesrgan", settings.realesrgan_bin, settings.realesrgan_args, result
        )
        if upscaled is None:
            _log.warning("enhance.upscale_skipped", requested=str(level), applied="basic")
            return result, applied
        result, applied = upscaled, EnhancementLevel.ENHANCE

    if level is EnhancementLevel.RESTORE:
        restored = _run_external_tool(
            "gfpgan", settings.gfpgan_bin, settings.gfpgan_args, result
        )
        if restored is None:
            _log.warning("enhance.restore_skipped", requested=str(level), applied="enhance")
            return result, applied
        result, applied = restored, EnhancementLevel.RESTORE

    return result, applied


def _basic_enhance(image: Image.Image) -> Image.Image:
    """White-balance, contrast and light denoising for faded old photos.

    Deliberately gentle — these are family photos, not posters; the goal is to
    undo ageing (yellow cast, faded contrast, scan grain), not to over-process.
    """
    rgb = np.asarray(image.convert("RGB"))

    balanced = _gray_world_white_balance(rgb)
    # Light chroma-preserving denoise to tame scanner/film grain.
    denoised = cv2.fastNlMeansDenoisingColored(balanced, None, h=3, hColor=3,
                                               templateWindowSize=7, searchWindowSize=21)
    # Stretch contrast, clipping 1% at each end to recover faded photos.
    return ImageOps.autocontrast(Image.fromarray(denoised), cutoff=1)


def _gray_world_white_balance(rgb: np.ndarray) -> np.ndarray:
    """Gray-world white balance: scale each channel so its mean matches the grand mean."""
    channels = rgb.astype(np.float32)
    means = channels.reshape(-1, 3).mean(axis=0)
    grand_mean = float(means.mean())
    # Avoid division by zero on degenerate (e.g. all-black) images.
    scale = np.where(means > 1e-6, grand_mean / means, 1.0)
    balanced = np.clip(channels * scale, 0, 255)
    return balanced.astype(np.uint8)


def _run_external_tool(
    name: str,
    binary: str | None,
    extra_args: str,
    image: Image.Image,
) -> Image.Image | None:
    """Run an external image tool (Real-ESRGAN / GFPGAN) over ``image``.

    Returns the processed image, or ``None`` if the tool is not configured, not
    found, or exits with an error — the caller then degrades gracefully.
    """
    if not binary:
        return None

    with tempfile.TemporaryDirectory(prefix="albumine-enh-") as tmp:
        in_path = Path(tmp) / "in.png"
        out_path = Path(tmp) / "out.png"
        image.convert("RGB").save(in_path, "PNG")

        command = [binary, "-i", str(in_path), "-o", str(out_path), *shlex.split(extra_args)]
        try:
            subprocess.run(
                command, capture_output=True, check=True, timeout=_SUBPROCESS_TIMEOUT
            )
        except FileNotFoundError:
            _log.warning("enhance.tool_not_found", tool=name, binary=binary)
            return None
        except subprocess.CalledProcessError as exc:
            _log.warning(
                "enhance.tool_failed", tool=name, returncode=exc.returncode,
                stderr=exc.stderr.decode(errors="replace")[:500] if exc.stderr else "",
            )
            return None
        except subprocess.TimeoutExpired:
            _log.warning("enhance.tool_timeout", tool=name, timeout=_SUBPROCESS_TIMEOUT)
            return None

        if not out_path.is_file():
            _log.warning("enhance.tool_no_output", tool=name)
            return None
        with Image.open(out_path) as produced:
            return produced.convert("RGB")
