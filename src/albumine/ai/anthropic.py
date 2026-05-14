"""Anthropic Claude vision provider.

Uses the Messages API with forced tool-use: the model must call the
``rueckseite_erfassen`` tool, whose input schema is the extraction schema — so
the response is guaranteed-structured rather than free text.

Cloud opt-in: this provider sends the image to Anthropic. The UI must make that
explicit to the user (data-privacy requirement).
"""

from __future__ import annotations

import base64
from typing import Any

from anthropic import AnthropicError, AsyncAnthropic
from pydantic import ValidationError

from albumine.ai.base import (
    AIProviderError,
    BackExtraction,
    ProviderHealth,
    VisionProvider,
)
from albumine.ai.prompts import (
    SYSTEM_PROMPT,
    TOOL_NAME,
    USER_INSTRUCTION,
    build_tool_definition,
)
from albumine.logging import get_logger

_log = get_logger(__name__)

_MAX_TOKENS = 1024


class AnthropicProvider(VisionProvider):
    """Vision extraction via the Anthropic Claude API."""

    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        client: AsyncAnthropic | None = None,
    ) -> None:
        self.model = model
        self._client = client or AsyncAnthropic(api_key=api_key)

    async def extract_back(
        self, image: bytes, *, mime_type: str = "image/jpeg"
    ) -> BackExtraction:
        encoded = base64.b64encode(image).decode("ascii")
        try:
            response = await self._client.messages.create(
                model=self.model,
                max_tokens=_MAX_TOKENS,
                system=SYSTEM_PROMPT,
                tools=[build_tool_definition()],
                tool_choice={"type": "tool", "name": TOOL_NAME},
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": mime_type,
                                    "data": encoded,
                                },
                            },
                            {"type": "text", "text": USER_INSTRUCTION},
                        ],
                    }
                ],
            )
        except AnthropicError as exc:
            raise AIProviderError(f"Anthropic request failed: {exc}") from exc

        extraction = _extraction_from_response(response)
        _log.info("anthropic.extracted", model=self.model)
        return extraction

    async def health_check(self) -> ProviderHealth:
        try:
            await self._client.models.list(limit=1)
        except AnthropicError as exc:
            return ProviderHealth(provider=self.name, healthy=False, detail=str(exc))
        return ProviderHealth(
            provider=self.name, healthy=True, detail="Anthropic API erreichbar"
        )

    async def aclose(self) -> None:
        await self._client.close()


def _extraction_from_response(response: Any) -> BackExtraction:
    """Pull the tool-use input out of a Claude Messages response."""
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "tool_use":
            try:
                return BackExtraction.model_validate(block.input)
            except ValidationError as exc:
                raise AIProviderError(
                    f"Claude tool input did not match the expected schema: {exc}"
                ) from exc
    raise AIProviderError("Claude response contained no tool_use block")
