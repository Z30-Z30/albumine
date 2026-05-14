"""Tests for the provider-agnostic AI data models."""

import json

import pytest

from albumine.ai.base import AIProviderError, BackExtraction
from albumine.parsing.date_parser import Confidence

_VALID = {
    "raw_text": "Hochzeit Anna & Hans, Zürich, Mai 1973",
    "date": {"iso": "1973-05", "original_text": "Mai 1973", "confidence": "high"},
    "location": "Zürich",
    "people": ["Anna", "Hans"],
    "event": "Hochzeit",
    "notes": None,
}


def test_from_raw_json_parses_plain_json():
    result = BackExtraction.from_raw_json(json.dumps(_VALID))
    assert result.raw_text == _VALID["raw_text"]
    assert result.date.iso == "1973-05"
    assert result.date.confidence is Confidence.HIGH
    assert result.people == ["Anna", "Hans"]
    assert result.location == "Zürich"
    assert result.notes is None


def test_from_raw_json_strips_markdown_fences():
    fenced = f"```json\n{json.dumps(_VALID)}\n```"
    result = BackExtraction.from_raw_json(fenced)
    assert result.event == "Hochzeit"


def test_from_raw_json_recovers_json_from_surrounding_prose():
    noisy = f"Hier ist das Ergebnis:\n{json.dumps(_VALID)}\nFertig."
    result = BackExtraction.from_raw_json(noisy)
    assert result.date.original_text == "Mai 1973"


def test_from_raw_json_rejects_non_json():
    with pytest.raises(AIProviderError):
        BackExtraction.from_raw_json("das ist überhaupt kein JSON")


def test_unknown_confidence_falls_back_to_low():
    data = {**_VALID, "date": {"iso": None, "original_text": "", "confidence": "sehr sicher"}}
    result = BackExtraction.from_raw_json(json.dumps(data))
    assert result.date.confidence is Confidence.LOW


def test_empty_strings_become_none():
    data = {**_VALID, "location": "   ", "event": "", "notes": "\n"}
    result = BackExtraction.from_raw_json(json.dumps(data))
    assert result.location is None
    assert result.event is None
    assert result.notes is None


def test_people_list_is_cleaned():
    data = {**_VALID, "people": ["  Anna  ", "", "   ", "Hans"]}
    result = BackExtraction.from_raw_json(json.dumps(data))
    assert result.people == ["Anna", "Hans"]


def test_defaults_for_minimal_payload():
    result = BackExtraction.from_raw_json("{}")
    assert result.raw_text == ""
    assert result.people == []
    assert result.date.iso is None
    assert result.date.confidence is Confidence.LOW
