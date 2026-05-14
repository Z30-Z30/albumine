"""AI layer: pluggable vision-LLM providers for photo-back extraction."""

from __future__ import annotations

from typing import TYPE_CHECKING

from albumine.ai.base import (
    AIProviderError,
    BackExtraction,
    ExtractedDate,
    ProviderHealth,
    VisionProvider,
)

if TYPE_CHECKING:
    from albumine.config import Settings


def build_provider(settings: Settings) -> VisionProvider:
    """Construct the vision provider selected by ``settings.ai_provider``.

    Raises:
        AIProviderError: If the selected provider is missing required config.
    """
    provider = settings.ai_provider
    if provider == "ollama":
        from albumine.ai.ollama import OllamaProvider

        return OllamaProvider(settings.ollama_host, settings.ollama_vision_model)

    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise AIProviderError(
                "AI-Provider 'anthropic' gewählt, aber ALBUMINE_ANTHROPIC_API_KEY "
                "ist nicht gesetzt"
            )
        from albumine.ai.anthropic import AnthropicProvider

        return AnthropicProvider(settings.anthropic_api_key, settings.anthropic_model)

    if provider == "openai_compat":
        if not settings.openai_base_url or not settings.openai_model:
            raise AIProviderError(
                "AI-Provider 'openai_compat' gewählt, aber ALBUMINE_OPENAI_BASE_URL "
                "oder ALBUMINE_OPENAI_MODEL ist nicht gesetzt"
            )
        from albumine.ai.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(
            settings.openai_base_url,
            settings.openai_model,
            api_key=settings.openai_api_key,
        )

    raise AIProviderError(f"unbekannter AI-Provider: {provider!r}")


__all__ = [
    "AIProviderError",
    "BackExtraction",
    "ExtractedDate",
    "ProviderHealth",
    "VisionProvider",
    "build_provider",
]
