"""Ollama vision provider.

Talks to a local (or LAN) Ollama server over its HTTP API, using JSON-schema
structured output (the ``format`` field) so the model returns parseable JSON.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

from albumine.ai.base import (
    AIProviderError,
    BackExtraction,
    ProviderHealth,
    VisionProvider,
)
from albumine.ai.prompts import (
    BACK_EXTRACTION_SCHEMA,
    SYSTEM_PROMPT,
    USER_INSTRUCTION,
)
from albumine.logging import get_logger

_log = get_logger(__name__)

_DEFAULT_TIMEOUT = 120.0  # vision models on CPU can be slow


class OllamaProvider(VisionProvider):
    """Vision extraction via a self-hosted Ollama server."""

    name = "ollama"

    def __init__(
        self,
        host: str,
        model: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._host = host.rstrip("/")
        self.model = model
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def extract_back(
        self, image: bytes, *, mime_type: str = "image/jpeg"
    ) -> BackExtraction:
        payload = self._build_payload(image)
        try:
            response = await self._client.post(f"{self._host}/api/chat", json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AIProviderError(f"Ollama request failed: {exc}") from exc

        content = response.json().get("message", {}).get("content", "")
        if not content:
            raise AIProviderError("Ollama returned an empty message content")
        _log.info("ollama.extracted", model=self.model)
        return BackExtraction.from_raw_json(content)

    async def health_check(self) -> ProviderHealth:
        try:
            response = await self._client.get(f"{self._host}/api/tags")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return ProviderHealth(provider=self.name, healthy=False, detail=str(exc))
        models = {m.get("name", "") for m in response.json().get("models", [])}
        model_present = any(name.startswith(self.model) for name in models)
        detail = (
            f"{self._host} erreichbar"
            if model_present
            else f"{self._host} erreichbar, aber Modell '{self.model}' nicht installiert"
        )
        return ProviderHealth(provider=self.name, healthy=model_present, detail=detail)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _build_payload(self, image: bytes) -> dict[str, Any]:
        encoded = base64.b64encode(image).decode("ascii")
        return {
            "model": self.model,
            "stream": False,
            "format": BACK_EXTRACTION_SCHEMA,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_INSTRUCTION, "images": [encoded]},
            ],
        }
