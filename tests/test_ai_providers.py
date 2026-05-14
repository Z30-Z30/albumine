"""Tests for the vision providers and the provider factory.

HTTP providers are exercised through ``httpx.MockTransport``; the Anthropic
provider is exercised through an injected fake client. No network is touched.
"""

import json
from types import SimpleNamespace

import httpx
import pytest
from anthropic import AnthropicError

from albumine.ai import build_provider
from albumine.ai.anthropic import AnthropicProvider
from albumine.ai.base import AIProviderError
from albumine.ai.ollama import OllamaProvider
from albumine.ai.openai_compat import OpenAICompatProvider
from albumine.config import Settings

_SAMPLE = {
    "raw_text": "Sommerferien 1968 am Meer",
    "date": {"iso": "1968", "original_text": "Sommer 1968", "confidence": "medium"},
    "location": "Rimini",
    "people": ["Oma", "Opa"],
    "event": "Sommerferien",
    "notes": None,
}
_IMAGE = b"\xff\xd8\xff\xe0fake-jpeg-bytes"


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- Ollama -----------------------------------------------------------------


async def test_ollama_extract_back():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"content": json.dumps(_SAMPLE)}})

    provider = OllamaProvider("http://ollama:11434", "llava", client=_mock_client(handler))
    result = await provider.extract_back(_IMAGE)

    assert result.location == "Rimini"
    assert result.people == ["Oma", "Opa"]
    assert captured["url"].endswith("/api/chat")
    assert captured["body"]["model"] == "llava"
    assert captured["body"]["stream"] is False
    assert "format" in captured["body"]  # JSON-schema structured output
    assert captured["body"]["messages"][1]["images"]  # image attached


async def test_ollama_http_error_raises_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    provider = OllamaProvider("http://ollama:11434", "llava", client=_mock_client(handler))
    with pytest.raises(AIProviderError):
        await provider.extract_back(_IMAGE)


async def test_ollama_health_check_reports_missing_model():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "mistral:latest"}]})

    provider = OllamaProvider("http://ollama:11434", "llava", client=_mock_client(handler))
    health = await provider.health_check()
    assert health.healthy is False
    assert "llava" in health.detail


async def test_ollama_health_check_ok_when_model_present():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "llava:latest"}]})

    provider = OllamaProvider("http://ollama:11434", "llava", client=_mock_client(handler))
    health = await provider.health_check()
    assert health.healthy is True


async def test_ollama_health_check_handles_unreachable_host():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    provider = OllamaProvider("http://ollama:11434", "llava", client=_mock_client(handler))
    health = await provider.health_check()
    assert health.healthy is False


# --- OpenAI-compatible ------------------------------------------------------


async def test_openai_compat_extract_back():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(_SAMPLE)}}]},
        )

    provider = OpenAICompatProvider(
        "http://vllm:8000/v1", "qwen-vl", api_key="secret", client=_mock_client(handler)
    )
    result = await provider.extract_back(_IMAGE)

    assert result.event == "Sommerferien"
    assert captured["url"].endswith("/chat/completions")
    assert captured["auth"] == "Bearer secret"
    assert captured["body"]["response_format"]["type"] == "json_schema"


async def test_openai_compat_unexpected_shape_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    provider = OpenAICompatProvider(
        "http://vllm:8000/v1", "qwen-vl", client=_mock_client(handler)
    )
    with pytest.raises(AIProviderError):
        await provider.extract_back(_IMAGE)


# --- Anthropic --------------------------------------------------------------


class _FakeMessages:
    def __init__(self, content):
        self._content = content

    async def create(self, **kwargs):
        return SimpleNamespace(content=self._content)


class _FakeModels:
    def __init__(self, ok: bool):
        self._ok = ok

    async def list(self, **kwargs):
        if not self._ok:
            raise AnthropicError("invalid api key")
        return SimpleNamespace(data=[])


class _FakeAnthropic:
    def __init__(self, *, content=None, models_ok=True):
        self.messages = _FakeMessages(content)
        self.models = _FakeModels(models_ok)

    async def close(self):
        pass


async def test_anthropic_extract_back_reads_tool_use_block():
    content = [
        SimpleNamespace(type="text", text="Ich nutze das Tool."),
        SimpleNamespace(type="tool_use", name="rueckseite_erfassen", input=_SAMPLE),
    ]
    provider = AnthropicProvider("key", "claude-opus-4-7", client=_FakeAnthropic(content=content))
    result = await provider.extract_back(_IMAGE)
    assert result.raw_text == "Sommerferien 1968 am Meer"
    assert result.people == ["Oma", "Opa"]


async def test_anthropic_without_tool_use_block_raises():
    content = [SimpleNamespace(type="text", text="kein Tool benutzt")]
    provider = AnthropicProvider("key", "claude-opus-4-7", client=_FakeAnthropic(content=content))
    with pytest.raises(AIProviderError):
        await provider.extract_back(_IMAGE)


async def test_anthropic_health_check_failure():
    provider = AnthropicProvider("key", "claude-opus-4-7", client=_FakeAnthropic(models_ok=False))
    health = await provider.health_check()
    assert health.healthy is False


async def test_anthropic_health_check_ok():
    provider = AnthropicProvider("key", "claude-opus-4-7", client=_FakeAnthropic(models_ok=True))
    health = await provider.health_check()
    assert health.healthy is True


# --- factory ----------------------------------------------------------------


def test_build_provider_ollama():
    provider = build_provider(Settings(ai_provider="ollama"))
    assert isinstance(provider, OllamaProvider)


def test_build_provider_anthropic_requires_api_key():
    with pytest.raises(AIProviderError):
        build_provider(Settings(ai_provider="anthropic", anthropic_api_key=None))


def test_build_provider_anthropic_with_key():
    provider = build_provider(
        Settings(ai_provider="anthropic", anthropic_api_key="sk-test")
    )
    assert isinstance(provider, AnthropicProvider)


def test_build_provider_openai_compat_requires_url_and_model():
    with pytest.raises(AIProviderError):
        build_provider(Settings(ai_provider="openai_compat"))


def test_build_provider_openai_compat_configured():
    provider = build_provider(
        Settings(
            ai_provider="openai_compat",
            openai_base_url="http://vllm:8000/v1",
            openai_model="qwen-vl",
        )
    )
    assert isinstance(provider, OpenAICompatProvider)
