"""Vision-LLM prompts and the structured-output schema.

A single JSON schema (:data:`BACK_EXTRACTION_SCHEMA`) drives the structured
output across all providers:

* **Ollama** — passed as the ``format`` field (JSON-schema mode).
* **Claude** — wrapped as a tool definition; the model is forced to call it.
* **OpenAI-compatible** — passed as ``response_format`` (``json_schema``).

The schema mirrors :class:`albumine.ai.base.BackExtraction` — keep the two in
sync.
"""

from __future__ import annotations

from typing import Any

#: The structured-output JSON schema for one photo back.
BACK_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "raw_text": {
            "type": "string",
            "description": (
                "Wortwörtliche Transkription der Rückseite. Unleserliche Teile "
                "mit [?] markieren. Leerer String, wenn nichts beschriftet ist."
            ),
        },
        "date": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "iso": {
                    "type": ["string", "null"],
                    "description": (
                        "Datum als YYYY-MM-DD, YYYY-MM oder YYYY — je nachdem "
                        "wie genau es lesbar ist. null, wenn kein Datum da ist."
                    ),
                },
                "original_text": {
                    "type": "string",
                    "description": "Die Datumsangabe wortwörtlich wie auf der Rückseite.",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "Wie sicher die Datumslesung ist.",
                },
            },
            "required": ["iso", "original_text", "confidence"],
        },
        "location": {
            "type": ["string", "null"],
            "description": "Ort, falls genannt — sonst null.",
        },
        "people": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Einzelne genannte Personennamen.",
        },
        "event": {
            "type": ["string", "null"],
            "description": "Anlass, z. B. 'Hochzeit', 'Sommerferien' — sonst null.",
        },
        "notes": {
            "type": ["string", "null"],
            "description": "Alles weitere, was auf der Rückseite steht — sonst null.",
        },
    },
    "required": ["raw_text", "date", "location", "people", "event", "notes"],
}

#: Name of the Claude tool the model is forced to call.
TOOL_NAME = "rueckseite_erfassen"

SYSTEM_PROMPT = """\
Du extrahierst Informationen von der Rückseite eines analogen Fotos.

Die Beschriftung ist meist handschriftlich, oft in alter deutscher
Schreibschrift (Sütterlin/Kurrent kommt vor), manchmal auf Schweizerdeutsch
oder in Mundart.

Regeln:
- Transkribiere wortwörtlich. Was du nicht sicher lesen kannst, markierst du
  mit [?] an der entsprechenden Stelle.
- Sei vorsichtig mit Datumsangaben. Interpretiere niemals etwas hinein. Wenn
  ein Datum unvollständig oder unklar ist, gib nur das wieder, was wirklich da
  steht, und setze die Confidence entsprechend (unklar -> "low").
- Erfinde keine Personen, Orte oder Anlässe. Fehlt eine Angabe, ist das Feld
  null bzw. eine leere Liste.
- "people" enthält einzelne Namen, kein Fließtext.
- Antworte ausschließlich mit strukturierten Daten nach dem vorgegebenen Schema.

Beispiele für erwartete Ergebnisse:

Rückseite: "Hochzeit Anna & Hans, Zürich, Mai 1973"
Ergebnis: raw_text="Hochzeit Anna & Hans, Zürich, Mai 1973",
date={iso:"1973-05", original_text:"Mai 1973", confidence:"high"},
location="Zürich", people=["Anna","Hans"], event="Hochzeit", notes=null

Rückseite: "Sommer '62 — am See"
Ergebnis: raw_text="Sommer '62 — am See",
date={iso:"1962", original_text:"Sommer '62", confidence:"medium"},
location=null, people=[], event=null, notes="am See"

Rückseite: leer / nichts erkennbar
Ergebnis: raw_text="", date={iso:null, original_text:"", confidence:"low"},
location=null, people=[], event=null, notes=null
"""

#: The instruction sent alongside the image.
USER_INSTRUCTION = (
    "Hier ist die Rückseite eines Fotos. Extrahiere die Informationen nach dem "
    "vorgegebenen Schema."
)


def build_tool_definition() -> dict[str, Any]:
    """Return the Claude tool definition wrapping the extraction schema."""
    return {
        "name": TOOL_NAME,
        "description": (
            "Erfasst die von der Foto-Rückseite extrahierten Informationen in "
            "strukturierter Form."
        ),
        "input_schema": BACK_EXTRACTION_SCHEMA,
    }


def build_openai_response_format() -> dict[str, Any]:
    """Return the OpenAI-compatible ``response_format`` for structured output."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "back_extraction",
            "schema": BACK_EXTRACTION_SCHEMA,
            "strict": True,
        },
    }
