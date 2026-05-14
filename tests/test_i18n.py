"""Tests for the UI internationalisation layer."""

import json
from pathlib import Path

import pytest

from albumine import i18n

_TRANSLATIONS_DIR = Path(i18n.__file__).parent / "web" / "translations"


def test_translate_known_key():
    assert i18n.translate("de", "nav.gallery") == "Galerie"
    assert i18n.translate("en", "nav.gallery") == "Gallery"


def test_translate_with_format_placeholder():
    assert i18n.translate("en", "footer.version", version="9.9") == "AlbuMine v9.9"


def test_translate_missing_key_returns_key():
    assert i18n.translate("de", "does.not.exist") == "does.not.exist"


def test_translate_falls_back_to_english(monkeypatch):
    # Simulate a language whose file is missing a key.
    monkeypatch.setitem(i18n._TABLES, "xx", {})
    monkeypatch.setitem(i18n.LANGUAGES, "xx", i18n.Language("xx", "Test"))
    assert i18n.translate("xx", "nav.gallery") == i18n.translate("en", "nav.gallery")


@pytest.mark.parametrize(
    ("code", "expected"),
    [("de", "de"), ("en", "en"), ("de-CH", "de"), ("xx", "en"), (None, "en")],
)
def test_normalise_language(code, expected):
    assert i18n.normalise_language(code) == expected


def test_translator_is_bound_to_language():
    t = i18n.translator("de")
    assert t("nav.settings") == "Einstellungen"


def test_every_language_file_covers_the_reference_keys():
    """All shipped languages must define every key in the de.json reference."""
    reference = set(json.loads((_TRANSLATIONS_DIR / "de.json").read_text("utf-8")))
    for code in i18n.LANGUAGES:
        path = _TRANSLATIONS_DIR / f"{code}.json"
        assert path.is_file(), f"missing translation file for {code}"
        keys = set(json.loads(path.read_text("utf-8")))
        assert keys == reference, f"{code}.json key set differs from de.json"
