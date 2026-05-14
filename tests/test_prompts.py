"""Tests for the AI prompt and structured-output schema."""

from albumine.ai.prompts import (
    BACK_EXTRACTION_SCHEMA,
    SYSTEM_PROMPT,
    TOOL_NAME,
    build_openai_response_format,
    build_tool_definition,
)


def test_schema_covers_all_extraction_fields():
    props = BACK_EXTRACTION_SCHEMA["properties"]
    assert set(props) == {"raw_text", "date", "location", "people", "event", "notes"}
    assert set(props["date"]["properties"]) == {"iso", "original_text", "confidence"}
    assert props["date"]["properties"]["confidence"]["enum"] == ["high", "medium", "low"]


def test_tool_definition_wraps_schema():
    tool = build_tool_definition()
    assert tool["name"] == TOOL_NAME
    assert tool["input_schema"] is BACK_EXTRACTION_SCHEMA


def test_openai_response_format_is_strict_json_schema():
    fmt = build_openai_response_format()
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] is BACK_EXTRACTION_SCHEMA


def test_system_prompt_is_german_and_mentions_key_constraints():
    assert "Rückseite" in SYSTEM_PROMPT
    # Must instruct the model not to invent dates and to mark unclear text.
    assert "[?]" in SYSTEM_PROMPT
    assert "Confidence" in SYSTEM_PROMPT
