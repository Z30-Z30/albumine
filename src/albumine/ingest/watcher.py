"""Watch-folder monitoring for the ``/input`` directory.

Uses ``watchdog`` (inotify on Linux, FSEvents on macOS). Filesystem events are
*debounced*: a burst of writes — e.g. a multi-file copy, or a scanner still
flushing a file — collapses into a single callback once the folder has been
quiet for ``settle_seconds``. The callback then triggers a full directory
re-scan, which naturally handles image pairs whose halves arrive separately.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from albumine.logging import get_logger

_log = get_logger(__name__)


class _DebouncedHandler(FileSystemEventHandler):
    """Collapses a burst of filesystem events into one debounced callback."""

    def __init__(self, callback: Callable[[], None], settle_seconds: float) -> None:
        self._callback = callback
        self._settle_seconds = settle_seconds
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._schedule()

    def _schedule(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._settle_seconds, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        try:
            self._callback()
        except Exception:  # noqa: BLE001 — a watcher must never die on a callback error
            _log.exception("watcher.callback_failed")

    def cancel_pending(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


class FolderWatcher:
    """Watches a folder and fires ``callback`` after a debounced quiet period.

    Example:
        >>> watcher = FolderWatcher(Path("/input"), on_change)
        >>> watcher.start()
        ...
        >>> watcher.stop()
    """

    def __init__(
        self,
        folder: Path,
        callback: Callable[[], None],
        *,
        settle_seconds: float = 2.0,
    ) -> None:
        self.folder = folder
        self._callback = callback
        self._handler = _DebouncedHandler(callback, settle_seconds)
        self._observer = Observer()
        self._started = False

    def start(self) -> None:
        """Begin watching. Creates the folder if it does not exist yet."""
        self.folder.mkdir(parents=True, exist_ok=True)
        self._observer.schedule(self._handler, str(self.folder), recursive=True)
        self._observer.start()
        self._started = True
        _log.info("watcher.started", folder=str(self.folder))

    def stop(self) -> None:
        """Stop watching and cancel any pending debounced callback."""
        if not self._started:
            return
        self._handler.cancel_pending()
        self._observer.stop()
        self._observer.join(timeout=5)
        self._started = False
        _log.info("watcher.stopped", folder=str(self.folder))

    def trigger_rescan(self) -> None:
        """Manually fire the callback immediately (e.g. a 'Re-Scan' UI button)."""
        _log.info("watcher.manual_rescan", folder=str(self.folder))
        self._callback()
