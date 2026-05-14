"""Tests for the database-backed settings override layer."""

from albumine.config import EnhancementLevel, Settings
from albumine.db.settings_store import (
    EDITABLE_SETTINGS,
    effective_settings,
    get_override,
    load_overrides,
    save_overrides,
    validate_overrides,
)


def test_load_overrides_empty(session_factory):
    assert load_overrides(session_factory) == {}


def test_save_and_load_roundtrip(session_factory):
    save_overrides(session_factory, {"jpeg_quality": "70", "ui_language": "en"})
    assert load_overrides(session_factory) == {"jpeg_quality": "70", "ui_language": "en"}
    assert get_override(session_factory, "ui_language") == "en"
    assert get_override(session_factory, "auto_crop") is None


def test_save_overrides_updates_existing(session_factory):
    save_overrides(session_factory, {"jpeg_quality": "70"})
    save_overrides(session_factory, {"jpeg_quality": "55"})
    assert get_override(session_factory, "jpeg_quality") == "55"


def test_effective_settings_without_overrides_returns_base(session_factory):
    base = Settings()
    assert effective_settings(base, session_factory) is base


def test_effective_settings_applies_override(session_factory):
    save_overrides(session_factory, {"jpeg_quality": "60", "ui_language": "fr"})
    effective = effective_settings(Settings(), session_factory)
    assert effective.jpeg_quality == 60
    assert effective.ui_language == "fr"


def test_effective_settings_coerces_types(session_factory):
    save_overrides(
        session_factory,
        {"auto_crop": "false", "default_enhancement_level": "enhance"},
    )
    effective = effective_settings(Settings(), session_factory)
    assert effective.auto_crop is False
    assert effective.default_enhancement_level is EnhancementLevel.ENHANCE


def test_effective_settings_empty_nullable_becomes_none(session_factory):
    save_overrides(session_factory, {"archive_dir": ""})
    effective = effective_settings(Settings(), session_factory)
    assert effective.archive_dir is None


def test_effective_settings_ignores_invalid_overrides(session_factory):
    # jpeg_quality must be 1..100 — a bad stored value must not crash startup.
    save_overrides(session_factory, {"jpeg_quality": "9999"})
    base = Settings()
    effective = effective_settings(base, session_factory)
    assert effective is base  # fell back to the base config


def test_validate_overrides():
    base = Settings()
    assert validate_overrides(base, {"jpeg_quality": "80"}) is None
    error = validate_overrides(base, {"jpeg_quality": "9999"})
    assert error is not None and "jpeg_quality" in error


def test_config_dir_is_not_editable():
    # config_dir is where the database lives — it can only come from the env.
    assert "config_dir" not in {f.key for f in EDITABLE_SETTINGS}
