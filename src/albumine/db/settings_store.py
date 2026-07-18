"""Runtime settings overrides — the layer behind the web settings panel.

The base configuration comes from environment variables
(:class:`albumine.config.Settings`). This module adds a database-backed override
layer: the settings panel writes :class:`~albumine.db.models.AppSetting` rows,
and :func:`effective_settings` merges them onto the base config.

Each editable field is declared in :data:`EDITABLE_SETTINGS` with metadata —
which category it belongs to, how to render it, whether it is a secret, and
whether a change needs a container restart to take effect (everything that is
read live by the pipeline applies immediately, and the AI provider is rebuilt
on change by the ProviderManager; paths, ports and logging are resolved once
at startup).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlmodel import select

from albumine.config import EnhancementLevel, Settings
from albumine.db.engine import SessionFactory
from albumine.db.models import AppSetting
from albumine.logging import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True)
class SettingField:
    """Metadata describing one editable configuration field."""

    key: str                       # matches a field name on Settings
    category: str
    kind: str                      # text | int | bool | choice | secret | language
    choices: tuple[str, ...] = field(default_factory=tuple)
    restart_required: bool = False  # change only takes effect after a restart
    nullable: bool = False          # an empty value means "unset" (None)


#: Display order of the setting categories.
CATEGORIES: tuple[str, ...] = ("general", "ai", "processing", "enhancement", "paths", "system")

#: Every configuration field the web settings panel may edit.
EDITABLE_SETTINGS: tuple[SettingField, ...] = (
    SettingField("ui_language", "general", "language"),
    SettingField("log_level", "general", "choice",
                 ("DEBUG", "INFO", "WARNING", "ERROR"), restart_required=True),
    SettingField("log_json", "general", "bool", restart_required=True),

    # AI fields apply live: the ProviderManager rebuilds the provider as soon
    # as one of them changes (see albumine.ai.manager).
    SettingField("ai_provider", "ai", "choice",
                 ("ollama", "anthropic", "openai_compat")),
    SettingField("ollama_host", "ai", "text"),
    SettingField("ollama_vision_model", "ai", "text"),
    SettingField("anthropic_api_key", "ai", "secret", nullable=True),
    SettingField("anthropic_model", "ai", "text"),
    SettingField("openai_base_url", "ai", "text", nullable=True),
    SettingField("openai_api_key", "ai", "secret", nullable=True),
    SettingField("openai_model", "ai", "text", nullable=True),

    SettingField("default_enhancement_level", "processing", "choice",
                 tuple(level.value for level in EnhancementLevel)),
    SettingField("jpeg_quality", "processing", "int"),
    SettingField("auto_crop", "processing", "bool"),
    SettingField("write_sidecar", "processing", "bool"),
    SettingField("archive_originals", "processing", "bool"),
    SettingField("ai_fallback_enabled", "processing", "bool"),

    SettingField("realesrgan_bin", "enhancement", "text", nullable=True),
    SettingField("realesrgan_args", "enhancement", "text"),
    SettingField("gfpgan_bin", "enhancement", "text", nullable=True),
    SettingField("gfpgan_args", "enhancement", "text"),

    # config_dir is intentionally NOT editable here: it is where this very
    # database lives, so it can only be set via the environment (bootstrap).
    SettingField("input_dir", "paths", "text", restart_required=True),
    SettingField("output_dir", "paths", "text", restart_required=True),
    SettingField("archive_dir", "paths", "text", restart_required=True, nullable=True),

    SettingField("webui_host", "system", "text", restart_required=True),
    SettingField("webui_port", "system", "int", restart_required=True),
    SettingField("redis_url", "system", "text", restart_required=True),
    SettingField("redis_connect_retries", "system", "int", restart_required=True),
)

_BY_KEY: dict[str, SettingField] = {f.key: f for f in EDITABLE_SETTINGS}

#: Setting keys whose value is sensitive and must not be echoed back to the UI.
SECRET_KEYS: frozenset[str] = frozenset(
    f.key for f in EDITABLE_SETTINGS if f.kind == "secret"
)


def get_field(key: str) -> SettingField | None:
    """Return the :class:`SettingField` for ``key`` if it is editable."""
    return _BY_KEY.get(key)


def load_overrides(session_factory: SessionFactory) -> dict[str, str]:
    """Return all stored override values, keyed by setting name."""
    with session_factory() as session:
        return {row.key: row.value for row in session.exec(select(AppSetting)).all()}


def get_override(session_factory: SessionFactory, key: str) -> str | None:
    """Return a single stored override value, or ``None`` if not set."""
    with session_factory() as session:
        row = session.get(AppSetting, key)
        return row.value if row is not None else None


def save_overrides(session_factory: SessionFactory, updates: dict[str, str]) -> None:
    """Persist override values (insert or update)."""
    with session_factory() as session:
        for key, value in updates.items():
            row = session.get(AppSetting, key)
            if row is None:
                session.add(AppSetting(key=key, value=value))
            else:
                row.value = value
                row.updated_at = datetime.now(UTC)
                session.add(row)
        session.commit()
    _log.info("settings.overrides_saved", keys=sorted(updates))


def _merge(base: Settings, overrides: dict[str, str]) -> dict[str, object]:
    """Build a Settings kwargs dict from the base config plus string overrides."""
    merged: dict[str, object] = base.model_dump()
    for key, raw in overrides.items():
        spec = _BY_KEY.get(key)
        if spec is None:
            continue  # ignore unknown / no-longer-editable keys
        merged[key] = None if (spec.nullable and raw == "") else raw
    return merged


def effective_settings(base: Settings, session_factory: SessionFactory) -> Settings:
    """Return the base settings with database overrides applied.

    Pydantic coerces the string override values to each field's type. If the
    stored overrides somehow fail validation, they are ignored and the base
    config is returned (the app must always be able to start).
    """
    overrides = load_overrides(session_factory)
    if not overrides:
        return base
    try:
        return Settings(**_merge(base, overrides))
    except ValidationError as exc:
        _log.warning("settings.invalid_overrides_ignored", error=str(exc))
        return base


def validate_overrides(base: Settings, overrides: dict[str, str]) -> str | None:
    """Validate a prospective override set; return an error message or ``None``."""
    try:
        Settings(**_merge(base, overrides))
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first.get("loc", ()))
        return f"{location}: {first.get('msg', 'ungültiger Wert')}"
    return None
