"""Tests for the debounced watch-folder."""

import threading

from albumine.ingest.watcher import FolderWatcher


def test_file_creation_triggers_debounced_callback(tmp_path):
    fired = threading.Event()
    watcher = FolderWatcher(tmp_path, fired.set, settle_seconds=0.2)
    watcher.start()
    try:
        (tmp_path / "scan.jpg").write_bytes(b"data")
        assert fired.wait(timeout=5.0), "callback was not fired after file creation"
    finally:
        watcher.stop()


def test_burst_of_writes_collapses_into_one_callback(tmp_path):
    calls: list[int] = []
    watcher = FolderWatcher(tmp_path, lambda: calls.append(1), settle_seconds=0.3)
    watcher.start()
    try:
        for i in range(5):
            (tmp_path / f"scan_{i}.jpg").write_bytes(b"data")
        # Wait comfortably past the settle window.
        threading.Event().wait(1.2)
        assert calls == [1], f"expected exactly one debounced callback, got {len(calls)}"
    finally:
        watcher.stop()


def test_trigger_rescan_fires_immediately(tmp_path):
    fired = threading.Event()
    watcher = FolderWatcher(tmp_path, fired.set, settle_seconds=10.0)
    watcher.trigger_rescan()
    assert fired.is_set()
