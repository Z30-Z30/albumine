"""Smoke tests for the CLI.

These exercise the argument parsing and command wiring on an empty environment
(no scans, empty DB) — the full pipeline path is covered by test_pipeline.py.
"""

import pytest

from albumine.cli import main


def test_cli_requires_a_command(capsys):
    with pytest.raises(SystemExit):
        main([])


def test_cli_scan_on_empty_input(capsys):
    exit_code = main(["scan"])
    assert exit_code == 0
    assert "Keine Scans" in capsys.readouterr().out


def test_cli_list_on_empty_db(capsys):
    exit_code = main(["list"])
    assert exit_code == 0
    assert "Noch keine" in capsys.readouterr().out
