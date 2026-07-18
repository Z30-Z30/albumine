"""Application configuration.

All settings are sourced from environment variables (Unraid convention — no
mandatory config files). Defaults are chosen to work out-of-the-box for a
single-container Selfhost setup.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

AIProvider = Literal["ollama", "anthropic", "openai_compat"]


class EnhancementLevel(StrEnum):
    """Image-enhancement intensity, each level building on the previous one."""

    NONE = "none"        # only crop / deskew (handled in front processing)
    BASIC = "basic"      # + colour/white-balance correction, contrast, denoise
    ENHANCE = "enhance"  # + Real-ESRGAN upscaling
    RESTORE = "restore"  # + GFPGAN face restoration


class Settings(BaseSettings):
    """Runtime configuration, populated from the environment.

    Environment variables are prefixed with ``ALBUMINE_`` (e.g.
    ``ALBUMINE_WEBUI_PORT``), except for a few well-known Unraid/linuxserver.io
    variables that are read verbatim.
    """

    model_config = SettingsConfigDict(
        env_prefix="ALBUMINE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Paths (mounted as Docker volumes) ---------------------------------
    input_dir: Path = Field(default=Path("/input"), description="Watch-folder for incoming scans.")
    output_dir: Path = Field(default=Path("/output"), description="Processed images land here.")
    config_dir: Path = Field(default=Path("/config"), description="SQLite DB, settings, logs.")
    archive_dir: Path | None = Field(
        default=None, description="Optional: keep original PDFs here."
    )

    # --- Web UI ------------------------------------------------------------
    webui_host: str = Field(default="0.0.0.0", description="Bind address for the web UI.")
    webui_port: int = Field(default=8765, description="Web UI port (Unraid default 8765).")

    # --- Task queue (ARQ / Redis) -----------------------------------------
    redis_url: str = Field(
        default="redis://localhost:6379", description="Redis connection URL for the ARQ queue."
    )
    redis_connect_retries: int = Field(
        default=5,
        ge=0,
        description="Connection attempts before the web app gives up on Redis (degraded mode).",
    )

    # --- AI backend --------------------------------------------------------
    ai_provider: AIProvider = Field(
        default="ollama", description="Default AI backend for vision/OCR extraction."
    )
    ollama_host: str = Field(
        default="http://localhost:11434", description="Base URL of the Ollama HTTP API."
    )
    ollama_vision_model: str = Field(default="llava", description="Ollama vision model name.")
    anthropic_api_key: str | None = Field(default=None, description="Anthropic API key (opt-in).")
    anthropic_model: str = Field(default="claude-opus-4-7", description="Anthropic vision model.")
    openai_base_url: str | None = Field(
        default=None, description="Base URL for an OpenAI-compatible endpoint."
    )
    openai_api_key: str | None = Field(
        default=None, description="API key for the OpenAI-compat endpoint."
    )
    openai_model: str | None = Field(
        default=None, description="Model name for the OpenAI-compat endpoint."
    )

    # --- Processing --------------------------------------------------------
    jpeg_quality: int = Field(default=90, ge=1, le=100, description="Output JPEG quality.")
    auto_crop: bool = Field(
        default=True, description="Detect and extract the photo from the scan background."
    )
    write_sidecar: bool = Field(
        default=False, description="Also write an .xmp sidecar next to each output image."
    )
    archive_originals: bool = Field(
        default=False, description="Keep source files in the archive directory after processing."
    )
    ai_fallback_enabled: bool = Field(
        default=True, description="Fall back to Tesseract OCR when the vision backend fails."
    )

    # --- Image enhancement -------------------------------------------------
    default_enhancement_level: EnhancementLevel = Field(
        default=EnhancementLevel.BASIC,
        description="Enhancement level applied to photos unless overridden per pair.",
    )
    realesrgan_bin: str | None = Field(
        default=None,
        description="Path to the Real-ESRGAN CLI binary (enables the 'enhance' level).",
    )
    realesrgan_args: str = Field(
        default="",
        description="Extra CLI args for Real-ESRGAN, appended after the -i/-o pair.",
    )
    gfpgan_bin: str | None = Field(
        default=None,
        description="Path to the GFPGAN CLI binary (enables the 'restore' level).",
    )
    gfpgan_args: str = Field(
        default="", description="Extra CLI args for GFPGAN, appended after -i/-o."
    )

    # --- Server control ----------------------------------------------------
    supervisor_url: str | None = Field(
        default="http://127.0.0.1:9001/RPC2",
        description=(
            "supervisord XML-RPC URL for the in-app restart button "
            "(all-in-one container). Set empty to disable the button."
        ),
    )

    # --- User interface ----------------------------------------------------
    ui_language: str = Field(
        default="de",
        description="UI language code (e.g. 'de', 'en'); overridable in the settings panel.",
    )

    # --- Logging -----------------------------------------------------------
    log_level: str = Field(default="INFO", description="Root log level.")
    log_json: bool = Field(default=True, description="Emit structured JSON logs.")

    @property
    def database_path(self) -> Path:
        """Filesystem path of the SQLite database inside the config volume."""
        return self.config_dir / "albumine.db"

    @property
    def database_url(self) -> str:
        """SQLAlchemy-style URL for the SQLite database."""
        return f"sqlite:///{self.database_path}"


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the process-wide :class:`Settings` singleton."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
