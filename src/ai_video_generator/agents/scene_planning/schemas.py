"""JSON Schemas for structured scene-planning agent output.

Responsibility: provide provider-facing JSON Schemas that match the validated
scene-planning contracts.
Workflow: included in every ``LLMRequest`` and used by provider adapters that
support structured output.
Inputs: contract enum definitions from the model and whiteboard layers.
Outputs: immutable-by-convention schema dictionaries for script and whiteboard
responses.
Interacts with: ``prompts.py``, both agents, and the future LLM provider adapter.
"""

from __future__ import annotations

from typing import Any

from ...models.scene import (
    MAX_KEY_POINT_SOURCE_IDS,
    MAX_KEY_POINT_TEXT_LENGTH,
    MAX_NARRATION_SEGMENT_TEXT_LENGTH,
    MAX_SCENE_KEY_POINTS,
    MAX_SCENE_SEGMENTS,
    MAX_SEGMENT_KEY_POINTS,
    SCENE_SCRIPT_SCHEMA_VERSION,
)
from ...whiteboard.board_layout import (
    MAX_BOARD_REGIONS,
    MIN_REGION_DIMENSION,
    BoardRegionKind,
)
from ...whiteboard.drawing_instructions import (
    MAX_ACTION_POINTS,
    MAX_DRAWING_ACTIONS,
    MAX_NON_WRITE_TEXT_LENGTH,
    MAX_WRITE_TEXT_LENGTH,
    DrawingActionType,
)
from ...whiteboard.handwriting_plan import MAX_HANDWRITING_CUES, HandwritingMode
from ...whiteboard.highlighting import MAX_HIGHLIGHT_CUES, HighlightStyle
from ...whiteboard.plan import WHITEBOARD_PLAN_SCHEMA_VERSION
from ...whiteboard.visual_continuity import (
    MAX_CONTINUITY_IDS,
    MAX_CONTINUITY_NOTES,
    MAX_CONTINUITY_NOTE_LENGTH,
    ContinuityMode,
)

_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_STRING = {"type": "string", "minLength": 1}
_ID = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "pattern": _ID_PATTERN,
    "not": {"pattern": r"[\r\n]"},
}


SCENE_SCRIPT_OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "SceneScript",
    "type": "object",
    "additionalProperties": False,
    "required": ["scene_id", "language", "segments", "key_points"],
    "properties": {
        "schema_version": {
            "type": "string",
            "enum": [SCENE_SCRIPT_SCHEMA_VERSION],
        },
        "scene_id": _ID,
        "language": {
            "type": "string",
            "minLength": 1,
            "maxLength": 64,
        },
        "segments": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_SCENE_SEGMENTS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "segment_id",
                    "text",
                    "estimated_duration_seconds",
                    "key_point_ids",
                ],
                "properties": {
                    "segment_id": _ID,
                    "text": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_NARRATION_SEGMENT_TEXT_LENGTH,
                    },
                    "estimated_duration_seconds": {
                        "type": "number",
                        "minimum": 0.5,
                        "maximum": 600,
                    },
                    "key_point_ids": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_SEGMENT_KEY_POINTS,
                        "uniqueItems": True,
                        "items": _ID,
                    },
                },
            },
        },
        "key_points": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_SCENE_KEY_POINTS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["key_point_id", "text", "source_ids"],
                "properties": {
                    "key_point_id": _ID,
                    "text": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_KEY_POINT_TEXT_LENGTH,
                    },
                    "source_ids": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_KEY_POINT_SOURCE_IDS,
                        "uniqueItems": True,
                        "items": _ID,
                    },
                    "importance": {
                        "type": "string",
                        "enum": ["core", "supporting"],
                    },
                },
            },
        },
        "delivery_notes": {
            "type": "array",
            "maxItems": 20,
            "items": {"type": "string", "minLength": 1, "maxLength": 1000},
        },
    },
}


WHITEBOARD_PLAN_OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "WhiteboardPlan",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "scene_id",
        "layout",
        "drawing",
        "handwriting",
        "highlights",
        "continuity",
    ],
    "properties": {
        "schema_version": {
            "type": "string",
            "enum": [WHITEBOARD_PLAN_SCHEMA_VERSION],
        },
        "scene_id": _ID,
        "layout": {
            "type": "object",
            "additionalProperties": False,
            "required": ["regions"],
            "properties": {
                "regions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_BOARD_REGIONS,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["region_id", "kind", "x", "y", "width", "height"],
                        "properties": {
                            "region_id": _ID,
                            "kind": {
                                "type": "string",
                                "enum": [item.value for item in BoardRegionKind],
                            },
                            "x": {"type": "number", "minimum": 0, "maximum": 1},
                            "y": {"type": "number", "minimum": 0, "maximum": 1},
                            "width": {
                                "type": "number",
                                "minimum": MIN_REGION_DIMENSION,
                                "maximum": 1,
                            },
                            "height": {
                                "type": "number",
                                "minimum": MIN_REGION_DIMENSION,
                                "maximum": 1,
                            },
                            "label": {"type": "string", "minLength": 1, "maxLength": 200},
                        },
                    },
                }
            },
        },
        "drawing": {
            "type": "object",
            "additionalProperties": False,
            "required": ["actions"],
            "properties": {
                "actions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_DRAWING_ACTIONS,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "action_id",
                            "action_type",
                            "segment_id",
                            "region_id",
                            "order",
                        ],
                        "properties": {
                            "action_id": _ID,
                            "action_type": {
                                "type": "string",
                                "enum": [item.value for item in DrawingActionType],
                            },
                            "segment_id": _ID,
                            "region_id": _ID,
                            "order": {"type": "integer", "minimum": 1},
                            "text": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": MAX_WRITE_TEXT_LENGTH,
                            },
                            "points": {
                                "type": "array",
                                "maxItems": MAX_ACTION_POINTS,
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["x", "y"],
                                    "properties": {
                                        "x": {"type": "number", "minimum": 0, "maximum": 1},
                                        "y": {"type": "number", "minimum": 0, "maximum": 1},
                                    },
                                },
                            },
                            "color": {"type": "string", "minLength": 1, "maxLength": 32},
                            "stroke_width": {
                                "type": "number",
                                "minimum": 0.5,
                                "maximum": 20,
                            },
                            "duration_seconds": {
                                "type": "number",
                                "minimum": 0.1,
                                "maximum": 600,
                            },
                            "label": {"type": "string", "minLength": 1, "maxLength": 200},
                        },
                        "allOf": [
                            {
                                "if": {
                                    "properties": {
                                        "action_type": {
                                            "enum": ["write_text", "write_equation"]
                                        }
                                    },
                                    "required": ["action_type"],
                                },
                                "then": {
                                    "required": ["text"],
                                    "properties": {
                                        "text": {"maxLength": MAX_WRITE_TEXT_LENGTH},
                                        "points": {"maxItems": 0},
                                    },
                                },
                                "else": {
                                    "properties": {
                                        "text": {
                                            "maxLength": MAX_NON_WRITE_TEXT_LENGTH
                                        }
                                    }
                                },
                            },
                            {
                                "if": {
                                    "properties": {
                                        "action_type": {
                                            "enum": [
                                                "draw_line",
                                                "draw_arrow",
                                                "draw_rectangle",
                                                "draw_circle",
                                            ]
                                        }
                                    },
                                    "required": ["action_type"],
                                },
                                "then": {
                                    "required": ["points"],
                                    "properties": {
                                        "points": {"minItems": 2, "maxItems": 2}
                                    },
                                },
                            },
                            {
                                "if": {
                                    "properties": {
                                        "action_type": {
                                            "enum": ["draw_path", "erase"]
                                        }
                                    },
                                    "required": ["action_type"],
                                },
                                "then": {
                                    "required": ["points"],
                                    "properties": {"points": {"minItems": 2}},
                                },
                            },
                        ],
                    },
                }
            },
        },
        "handwriting": {
            "type": "object",
            "additionalProperties": False,
            "required": ["cues"],
            "properties": {
                "cues": {
                    "type": "array",
                    "maxItems": MAX_HANDWRITING_CUES,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["cue_id", "action_id", "mode", "order"],
                        "properties": {
                            "cue_id": _ID,
                            "action_id": _ID,
                            "mode": {
                                "type": "string",
                                "enum": [item.value for item in HandwritingMode],
                            },
                            "order": {"type": "integer", "minimum": 1},
                            "reveal_duration_seconds": {
                                "type": "number",
                                "minimum": 0.1,
                                "maximum": 600,
                            },
                        },
                    },
                }
            },
        },
        "highlights": {
            "type": "object",
            "additionalProperties": False,
            "required": ["cues"],
            "properties": {
                "cues": {
                    "type": "array",
                    "maxItems": MAX_HIGHLIGHT_CUES,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "highlight_id",
                            "target_action_id",
                            "segment_id",
                            "style",
                            "order",
                        ],
                        "properties": {
                            "highlight_id": _ID,
                            "target_action_id": _ID,
                            "segment_id": _ID,
                            "style": {
                                "type": "string",
                                "enum": [item.value for item in HighlightStyle],
                            },
                            "order": {"type": "integer", "minimum": 1},
                            "color": {"type": "string", "minLength": 1, "maxLength": 32},
                            "duration_seconds": {
                                "type": "number",
                                "minimum": 0.1,
                                "maximum": 600,
                            },
                        },
                    },
                }
            },
        },
        "continuity": {
            "type": "object",
            "additionalProperties": False,
            "required": ["mode"],
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": [item.value for item in ContinuityMode],
                },
                "carry_over_action_ids": {
                    "type": "array",
                    "maxItems": MAX_CONTINUITY_IDS,
                    "uniqueItems": True,
                    "items": _ID,
                },
                "clear_region_ids": {
                    "type": "array",
                    "maxItems": MAX_CONTINUITY_IDS,
                    "uniqueItems": True,
                    "items": _ID,
                },
                "notes": {
                    "type": "array",
                    "maxItems": MAX_CONTINUITY_NOTES,
                    "items": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_CONTINUITY_NOTE_LENGTH,
                    },
                },
            },
        },
        "presentation_notes": {
            "type": "array",
            "maxItems": 20,
            "items": {"type": "string", "minLength": 1, "maxLength": 1000},
        },
    },
}


__all__ = ["SCENE_SCRIPT_OUTPUT_SCHEMA", "WHITEBOARD_PLAN_OUTPUT_SCHEMA"]
