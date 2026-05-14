"""OpenAI-compatible vision provider.

Targets any endpoint that speaks the OpenAI ``/chat/completions`` API — e.g. a
local vLLM server or another self-hosted provider. Uses ``response_format`` with
a JSON schema for structured output.
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
    SYSTEM_PROMPT,
    USER_INSTRUCTION,
    build_openai_response_format,
)
from albumine.logging import get_logger

_log = get_logger(__name__)

_DEFAULT_TIMEOUT = 120.0


class OpenAICompatProvider(VisionProvider):
    """Vision extraction via an OpenAI-compatible chat-completions endpoint."""

    name = "openai_compat"

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def extract_back(
        self, image: bytes, *, mime_type: str = "image/jpeg"
    ) -> BackExtraction:
        payload = self._build_payload(image, mime_type)
        try:
            response = await self._client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AIProviderError(f"OpenAI-compatible request failed: {exc}") from exc

        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderError(
                f"unexpected OpenAI-compatible response shape: {exc}"
            ) from exc
        if not content:
            raise AIProviderError("OpenAI-compatible endpoint returned empty content")
        _log.info("openai_compat.extracted", model=self.model)
        return BackExtraction.from_raw_json(content)

    async def health_check(self) -> ProviderHealth:
        try:
            response = await self._client.get(
                f"{self._base_url}/models", headers=self._headers()
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return ProviderHealth(provider=self.name, healthy=False, detail=str(exc))
        return ProviderHealth(
            provider=self.name, healthy=True, detail=f"{self._base_url} erreichbar"
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    def _build_payload(self, image: bytes, mime_type: str) -> dict[str, Any]:
        data_url = f"data:{mime_type};base64,{base64.b64encode(image).decode('ascii')}"
        return {
            "model": self.model,
            "response_format": build_openai_response_format(),
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": USER_INSTRUCTION},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        }
