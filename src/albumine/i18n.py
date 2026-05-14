"""Lightweight UI internationalisation.

Translations live as flat JSON files in ``web/translations/<code>.json`` — one
file per language. Adding a language is just dropping in a new file and listing
it in :data:`LANGUAGES`. No gettext / Babel dependency: a selfhost app with a
small, static set of UI strings does not need the machinery.

``de.json`` and ``en.json`` are the maintained reference files; the rest are
best-effort translations. Missing keys fall back to English, then to the key
itself, so the UI never breaks on an incomplete file.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from albumine.logging import get_logger

_log = get_logger(__name__)

_TRANSLATIONS_DIR = Path(__file__).parent / "web" / "translations"
FALLBACK_LANGUAGE = "en"


@dataclass(frozen=True)
class Language:
    """A selectable UI language."""

    code: str
    native_name: str
    text_direction: str = "ltr"


#: Shipped languages. New languages: add an entry here + a ``<code>.json`` file.
LANGUAGES: dict[str, Language] = {
    "de": Language("de", "Deutsch"),
    "en": Language("en", "English"),
    "fr": Language("fr", "Français"),
    "es": Language("es", "Español"),
    "it": Language("it", "Italiano"),
    "pt": Language("pt", "Português"),
    "nl": Language("nl", "Nederlands"),
    "pl": Language("pl", "Polski"),
    "ru": Language("ru", "Русский"),
    "uk": Language("uk", "Українська"),
    "cs": Language("cs", "Čeština"),
    "sv": Language("sv", "Svenska"),
    "tr": Language("tr", "Türkçe"),
    "zh-Hans": Language("zh-Hans", "简体中文"),
    "ja": Language("ja", "日本語"),
    "ko": Language("ko", "한국어"),
}


def _load_tables() -> dict[str, dict[str, str]]:
    tables: dict[str, dict[str, str]] = {}
    for code in LANGUAGES:
        path = _TRANSLATIONS_DIR / f"{code}.json"
        if not path.is_file():
            tables[code] = {}
            continue
        try:
            tables[code] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            _log.warning("i18n.bad_translation_file", file=str(path), error=str(exc))
            tables[code] = {}
    return tables


_TABLES: dict[str, dict[str, str]] = _load_tables()


def available_languages() -> list[Language]:
    """Return all shipped languages, in declaration order."""
    return list(LANGUAGES.values())


def normalise_language(code: str | None) -> str:
    """Map an arbitrary code to a supported one (falls back to English)."""
    if code and code in LANGUAGES:
        return code
    if code:
        base = code.split("-")[0]
        if base in LANGUAGES:
            return base
    return FALLBACK_LANGUAGE


def translate(lang: str, key: str, /, **fmt: object) -> str:
    """Look up ``key`` for ``lang``, falling back to English then the key itself."""
    text = _TABLES.get(lang, {}).get(key)
    if text is None:
        text = _TABLES.get(FALLBACK_LANGUAGE, {}).get(key, key)
    if fmt:
        try:
            text = text.format(**fmt)
        except (KeyError, IndexError, ValueError):
            pass  # a malformed template string must not crash a page render
    return text


def translator(lang: str) -> Callable[..., str]:
    """Return a ``t(key, **fmt)`` callable bound to a (normalised) language."""
    normalised = normalise_language(lang)

    def _t(key: str, /, **fmt: object) -> str:
        return translate(normalised, key, **fmt)

    return _t
