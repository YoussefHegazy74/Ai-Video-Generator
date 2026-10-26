"""JSON Schema for structured Example & Visual Agent output.

Responsibility: provide a provider-facing JSON Schema that matches the validated
``VisualPlan`` contract.
Workflow: included in every ``LLMRequest`` and used by provider adapters that
support structured output.
Inputs: visual-plan enum definitions and runtime limits from the model layer.
Outputs: a schema dictionary for visual-plan responses.
Interacts with: ``prompts.py``, ``visual_design_agent.py``, and the future LLM
provider adapter.
"""

from __future__ import annotations

from typing import Any

from ...models.visual_plan import (
    MAX_VISUAL_ACCESSIBILITY_NOTES,
    MAX_VISUAL_ACCESSIBILITY_NOTE_LENGTH,
    MAX_VISUAL_DESCRIPTION_LENGTH,
    MAX_VISUAL_EXACT_TEXT_LENGTH,
    VISUAL_PLAN_SCHEMA_VERSION,
    VisualKind,
    VisualPurpose,
)

_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_ID = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "pattern": _ID_PATTERN,
    "not": {"pattern": r"[\r\n]"},
}
_TEXT = {
    "type": "string",
    "minLength": 1,
    "pattern": r"\S",
}


VISUAL_PLAN_OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "VisualPlan",
    "type": "object",
    "additionalProperties": False,
    "required": ["scene_id", "items"],
    "properties": {
        "schema_version": {
            "type": "string",
            "enum": [VISUAL_PLAN_SCHEMA_VERSION],
        },
        "scene_id": _ID,
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["visual_id", "purpose", "kind", "description"],
                "properties": {
                    "visual_id": _ID,
                    "purpose": {
                        "type": "string",
                        "enum": [item.value for item in VisualPurpose],
                    },
                    "kind": {
                        "type": "string",
                        "enum": [item.value for item in VisualKind],
                    },
                    "description": {
                        **_TEXT,
                        "maxLength": MAX_VISUAL_DESCRIPTION_LENGTH,
                    },
                    "segment_ids": {
                        "type": "array",
                        "uniqueItems": True,
                        "items": _ID,
                    },
                    "key_point_ids": {
                        "type": "array",
                        "uniqueItems": True,
                        "items": _ID,
                    },
                    "source_ids": {
                        "type": "array",
                        "uniqueItems": True,
                        "items": _ID,
                    },
                    "exact_text": {
                        "type": "array",
                        "items": {
                            **_TEXT,
                            "maxLength": MAX_VISUAL_EXACT_TEXT_LENGTH,
                        },
                    },
                    "accessibility_notes": {
                        "type": "array",
                        "maxItems": MAX_VISUAL_ACCESSIBILITY_NOTES,
                        "items": {
                            **_TEXT,
                            "maxLength": MAX_VISUAL_ACCESSIBILITY_NOTE_LENGTH,
                        },
                    },
                },
            },
        },
    },
}


__all__ = ["VISUAL_PLAN_OUTPUT_SCHEMA"]
