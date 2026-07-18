"""Live provider resolution.

The vision provider used to be constructed once at startup, so changing any
AI setting (provider choice, Ollama host/model, API keys) required a container
restart. :class:`ProviderManager` instead builds the provider lazily from the
current effective settings and rebuilds it whenever one of those settings
changes — the settings panel applies immediately.
"""

from __future__ import annotations

import asyncio

from albumine.ai import build_provider
from albumine.ai.base import AIProviderError, ProviderHealth, VisionProvider
from albumine.config import Settings
from albumine.db.engine import SessionFactory
from albumine.db.settings_store import effective_settings
from albumine.logging import get_logger

_log = get_logger(__name__)

#: Settings fields that determine which provider instance must be running.
_AI_FIELDS = (
    "ai_provider",
    "ollama_host",
    "ollama_vision_model",
    "anthropic_api_key",
    "anthropic_model",
    "openai_base_url",
    "openai_api_key",
    "openai_model",
)


class ProviderManager:
    """Builds the vision provider lazily; rebuilds it when AI settings change."""

    def __init__(self, base: Settings, session_factory: SessionFactory) -> None:
        self._base = base
        self._session_factory = session_factory
        self._provider: VisionProvider | None = None
        self._fingerprint: tuple[object, ...] | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> VisionProvider:
        """Return a provider matching the current effective settings.

        Raises:
            AIProviderError: If the configured provider is missing required
                config (e.g. Anthropic selected without an API key).
        """
        settings = effective_settings(self._base, self._session_factory)
        fingerprint = tuple(getattr(settings, name) for name in _AI_FIELDS)
        async with self._lock:
            if self._provider is not None and fingerprint == self._fingerprint:
                return self._provider
            if self._provider is not None:
                await self._provider.aclose()
                self._provider = None
                self._fingerprint = None
                _log.info("provider.settings_changed", provider=settings.ai_provider)
            provider = build_provider(settings)
            self._provider = provider
            self._fingerprint = fingerprint
            _log.info("provider.built", provider=provider.name, model=provider.model)
            return provider

    async def health(self) -> ProviderHealth:
        """Health of the currently configured provider; never raises."""
        settings = effective_settings(self._base, self._session_factory)
        try:
            provider = await self.get()
        except AIProviderError as exc:
            return ProviderHealth(
                provider=settings.ai_provider, healthy=False, detail=str(exc)
            )
        return await provider.health_check()

    async def aclose(self) -> None:
        """Release the current provider (HTTP clients etc.)."""
        async with self._lock:
            if self._provider is not None:
                await self._provider.aclose()
                self._provider = None
                self._fingerprint = None
