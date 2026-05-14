"""Content hashing for deduplication and stable pair identifiers.

Idempotency requirement: re-ingesting the same source material must not create
duplicates. We achieve this by deriving identifiers from file *content* (SHA-256)
rather than from paths or timestamps.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

from albumine.ingest.models import PageRef

_CHUNK_SIZE = 1 << 20  # 1 MiB


def file_sha256(path: Path) -> str:
    """Return the hex SHA-256 digest of a file's contents."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def make_pair_id(
    front: PageRef, back: PageRef | None, source_files: Iterable[Path]
) -> str:
    """Derive a stable 16-char identifier for a scan pair.

    The id is a function of the source files' contents plus the front/back page
    indices, so the same scan always maps to the same id — even across restarts
    or re-scans — while distinct pages of one multi-page PDF stay distinct.
    """
    unique_sources = sorted(set(source_files))
    digests = [file_sha256(path) for path in unique_sources]
    parts = [
        "|".join(digests),
        f"front={front.page_index}",
        f"back={back.page_index if back is not None else None}",
    ]
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:16]
