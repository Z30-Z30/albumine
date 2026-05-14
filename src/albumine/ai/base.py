"""Provider-agnostic interface and data models for the vision-LLM layer.

All providers (Ollama, Claude, OpenAI-compatible) implement
:class:`VisionProvider` and return the same :class:`BackExtraction` model, so the
rest of the pipeline never needs to know which backend produced the data
(Strategy pattern).
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, Field, ValidationError, field_validator

from albumine.parsing.date_parser import Confidence


class AIProviderError(RuntimeError):
    """Raised when an AI backend is unreachable or returns an unusable response."""


class ExtractedDate(BaseModel):
    """The date as the vision model read it from the photo back.

    This is the model's *own* reading. The pipeline still runs the deterministic
    :func:`albumine.parsing.date_parser.parse_date` over ``original_text`` to get
    a trustworthy EXIF timestamp — see the processing phase.
    """

    iso: str | None = None
    original_text: str = ""
    confidence: Confidence = Confidence.LOW

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, value: Any) -> str:
        """Be lenient: unknown / malformed confidence values fall back to 'low'."""
        if isinstance(value, str) and value.strip().lower() in {"high", "medium", "low"}:
            return value.strip().lower()
        return "low"

    @field_validator("iso", "original_text", mode="before")
    @classmethod
    def _normalise_strings(cls, value: Any) -> Any:
        if value is None:
            return value
        return str(value).strip()


class BackExtraction(BaseModel):
    """Structured information extracted from the back of a photo."""

    raw_text: str = ""
    date: ExtractedDate = Field(default_factory=ExtractedDate)
    location: str | None = None
    people: list[str] = Field(default_factory=list)
    event: str | None = None
    notes: str | None = None

    @field_validator("location", "event", "notes", mode="before")
    @classmethod
    def _empty_to_none(cls, value: Any) -> Any:
        """Treat empty / whitespace-only strings as 'not present'."""
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("people", mode="before")
    @classmethod
    def _clean_people(cls, value: Any) -> list[str]:
        """Drop empty entries and trim whitespace from person names."""
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @classmethod
    def from_raw_json(cls, text: str) -> BackExtraction:
        """Parse a model's raw text response into a :class:`BackExtraction`.

        Tolerates Markdown code fences and surrounding prose — some models wrap
        their JSON despite being asked not to.

        Raises:
            AIProviderError: If no valid JSON object can be recovered.
        """
        data = _extract_json_object(text)
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            raise AIProviderError(
                f"model response did not match the expected schema: {exc}"
            ) from exc


def _extract_json_object(text: str) -> dict[str, Any]:
    """Recover a JSON object from a possibly fenced / prose-wrapped string."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    candidates = [cleaned]
    brace_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if brace_match:
        candidates.append(brace_match.group(0))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise AIProviderError(
        f"could not parse a JSON object from the model response: {text[:200]!r}"
    )


class ProviderHealth(BaseModel):
    """Result of a provider health check, surfaced on the status dashboard."""

    provider: str
    healthy: bool
    detail: str = ""


class VisionProvider(ABC):
    """Strategy interface for a vision-LLM backend."""

    #: Stable provider identifier, matches ``Settings.ai_provider`` values.
    name: ClassVar[str]

    #: The model identifier in use (set by each provider in ``__init__``).
    model: str

    @abstractmethod
    async def extract_back(
        self, image: bytes, *, mime_type: str = "image/jpeg"
    ) -> BackExtraction:
        """Extract structured information from a photo-back image.

        Raises:
            AIProviderError: On transport failure or an unusable response.
        """

    @abstractmethod
    async def health_check(self) -> ProviderHealth:
        """Report whether the backend is reachable. Never raises."""

    async def aclose(self) -> None:  # noqa: B027 — intentionally an optional hook
        """Release any held resources (HTTP clients etc.). Default: no-op."""
