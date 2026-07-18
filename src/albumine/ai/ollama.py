"""Ollama vision provider.

Talks to a local (or LAN) Ollama server over its HTTP API, using JSON-schema
structured output (the ``format`` field) so the model returns parseable JSON.
"""

from __future__ import annotations

import base64
import json
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


def _error_detail(response: httpx.Response) -> str | None:
    """Extract Ollama's error message from an error response body.

    Ollama returns ``{"error": <detail>}`` where ``<detail>`` is plain text, a
    nested error object, or (0.3x multimodal rejections) a JSON-encoded string
    of such an object — unwrap all three to the human-readable message.
    """
    try:
        error = response.json().get("error")
    except ValueError:
        return response.text[:200] or None
    if isinstance(error, str):
        try:
            error = json.loads(error)
        except ValueError:
            return error
    if isinstance(error, dict):
        inner = error.get("error", error)
        if isinstance(inner, dict):
            return str(inner.get("message") or inner)
        return str(inner)
    return str(error) if error else None


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
        except httpx.HTTPStatusError as exc:
            detail = _error_detail(exc.response)
            raise AIProviderError(
                f"Ollama request failed ({exc.response.status_code}): {detail or exc}"
            ) from exc
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
        if not model_present:
            return ProviderHealth(
                provider=self.name,
                healthy=False,
                detail=f"{self._host} erreichbar, aber Modell '{self.model}' nicht installiert",
            )
        if not await self._supports_vision():
            return ProviderHealth(
                provider=self.name,
                healthy=False,
                detail=(
                    f"Modell '{self.model}' unterstützt keine Bilder (Vision) — "
                    "bitte ein Vision-Modell wählen"
                ),
            )
        return ProviderHealth(
            provider=self.name, healthy=True, detail=f"{self._host} erreichbar"
        )

    async def _supports_vision(self) -> bool:
        """Whether the configured model accepts images.

        Uses ``/api/show`` capabilities; treats them as supportive when the
        Ollama version does not report capabilities (older releases).
        """
        try:
            response = await self._client.post(
                f"{self._host}/api/show", json={"model": self.model}
            )
            response.raise_for_status()
            capabilities = response.json().get("capabilities") or []
        except httpx.HTTPError:
            return True
        return not capabilities or "vision" in capabilities

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
