"""Tests for the live provider resolution (albumine.ai.manager)."""

from albumine.ai.base import AIProviderError
from albumine.ai.manager import ProviderManager
from albumine.db.settings_store import save_overrides


async def test_manager_reuses_provider_while_settings_unchanged(
    app_settings, session_factory
):
    manager = ProviderManager(app_settings, session_factory)
    first = await manager.get()
    second = await manager.get()
    assert second is first
    await manager.aclose()


async def test_manager_rebuilds_provider_on_setting_change(
    app_settings, session_factory
):
    manager = ProviderManager(app_settings, session_factory)
    first = await manager.get()
    assert first.name == "ollama"

    save_overrides(session_factory, {"ollama_vision_model": "llava:13b"})
    second = await manager.get()

    assert second is not first
    assert second.model == "llava:13b"
    assert first._client.is_closed  # the old provider was released
    await manager.aclose()


async def test_manager_health_reports_misconfigured_provider(
    app_settings, session_factory
):
    # Anthropic selected without an API key: get() raises, health() must not.
    save_overrides(session_factory, {"ai_provider": "anthropic"})
    manager = ProviderManager(app_settings, session_factory)

    try:
        await manager.get()
        raise AssertionError("expected AIProviderError")
    except AIProviderError:
        pass

    health = await manager.health()
    assert health.healthy is False
    assert health.provider == "anthropic"
    assert health.detail
    await manager.aclose()


async def test_manager_recovers_after_misconfiguration(app_settings, session_factory):
    """A broken provider config must not wedge the manager: fixing the
    settings makes the next get() succeed."""
    manager = ProviderManager(app_settings, session_factory)
    assert (await manager.get()).name == "ollama"

    save_overrides(session_factory, {"ai_provider": "anthropic"})
    try:
        await manager.get()
    except AIProviderError:
        pass

    save_overrides(session_factory, {"ai_provider": "ollama"})
    provider = await manager.get()
    assert provider.name == "ollama"
    await manager.aclose()
