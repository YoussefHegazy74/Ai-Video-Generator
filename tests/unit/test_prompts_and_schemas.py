"""Unit tests for scene-planning prompts and JSON Schemas.

Responsibility: verify that prompts preserve selected pages and allowed source
IDs, prohibit on-screen instructors, and that schemas expose the required
structured fields for both agents.
Workflow: fast unit tests without provider calls.
Inputs: deterministic fixtures from ``tests/conftest.py``.
Outputs: pytest assertions only.
Interacts with: ``prompts.py``, ``schemas.py``, and the shared contracts.
"""

from __future__ import annotations

import copy
import json

import pytest

from ai_video_generator.agents.scene_planning.prompts import (
    SCRIPT_PROMPT_VERSION,
    WHITEBOARD_PROMPT_VERSION,
    build_script_prompt,
    build_whiteboard_prompt,
)
from ai_video_generator.agents.scene_planning.schemas import (
    SCENE_SCRIPT_OUTPUT_SCHEMA,
    WHITEBOARD_PLAN_OUTPUT_SCHEMA,
)
from ai_video_generator.models.scene import (
    MAX_KEY_POINT_SOURCE_IDS,
    MAX_KEY_POINT_TEXT_LENGTH,
    MAX_NARRATION_SEGMENT_TEXT_LENGTH,
    MAX_SCENE_KEY_POINTS,
    MAX_SCENE_SEGMENTS,
    MAX_SEGMENT_KEY_POINTS,
    SceneScript,
)
from ai_video_generator.whiteboard.board_layout import (
    MAX_BOARD_REGIONS,
    MIN_REGION_DIMENSION,
)
from ai_video_generator.whiteboard.drawing_instructions import (
    MAX_ACTION_POINTS,
    MAX_DRAWING_ACTIONS,
    MAX_NON_WRITE_TEXT_LENGTH,
    MAX_WRITE_TEXT_LENGTH,
)
from ai_video_generator.whiteboard.handwriting_plan import MAX_HANDWRITING_CUES
from ai_video_generator.whiteboard.highlighting import MAX_HIGHLIGHT_CUES
from ai_video_generator.whiteboard.plan import WhiteboardPlan
from ai_video_generator.whiteboard.visual_continuity import (
    MAX_CONTINUITY_IDS,
    MAX_CONTINUITY_NOTES,
    MAX_CONTINUITY_NOTE_LENGTH,
)


def test_script_prompt_preserves_pages_sources_and_preferences(planning_input):
    prompt = build_script_prompt(planning_input)

    assert SCRIPT_PROMPT_VERSION == "1.0.0"
    assert "Script Agent" in prompt.system_prompt
    assert "Do not describe an on-screen" in prompt.system_prompt
    assert "instructor" in prompt.system_prompt
    assert "10-20" in prompt.user_prompt
    assert "22-24" in prompt.user_prompt
    assert "src-1" in prompt.user_prompt
    assert "src-2" in prompt.user_prompt
    assert '"page_number": 12' in prompt.user_prompt
    assert '"page_number": 23' in prompt.user_prompt
    assert '"audience_level": "beginner"' in prompt.user_prompt
    assert '"teaching_style": "football"' in prompt.user_prompt
    assert "untrusted data" in prompt.user_prompt


def test_whiteboard_prompt_preserves_script_anchors(planning_input, script_payload):
    script = SceneScript.from_dict(script_payload)
    prompt = build_whiteboard_prompt(planning_input, script)

    assert WHITEBOARD_PROMPT_VERSION == "1.0.0"
    assert "Whiteboard Director Agent" in prompt.system_prompt
    assert "Never include an instructor" in prompt.system_prompt
    assert "fresh_board" in prompt.system_prompt
    assert "seg-1" in prompt.user_prompt
    assert "seg-2" in prompt.user_prompt
    assert "seg-3" in prompt.user_prompt
    assert "Previous action_ids: none" in prompt.user_prompt
    assert "Previous region_ids: none" in prompt.user_prompt
    assert "Validated script (authoritative" in prompt.user_prompt
    assert "10-20" in prompt.user_prompt
    assert "22-24" in prompt.user_prompt


def test_whiteboard_prompt_includes_previous_plan_context(
    planning_input, script_payload, whiteboard_payload
):
    script = SceneScript.from_dict(script_payload)
    previous = WhiteboardPlan.from_dict(whiteboard_payload)

    prompt = build_whiteboard_prompt(planning_input, script, previous)

    assert "Previous action_ids: act-1, act-2, act-3, act-4" in prompt.user_prompt
    assert "Previous region_ids: title, main, summary" in prompt.user_prompt
    assert '"scene_id": "scene-001"' in prompt.user_prompt
    assert "act-4" in prompt.user_prompt
    assert "carry_over_action_ids" in prompt.user_prompt


def test_schemas_are_serializable_and_declare_required_fields():
    script_schema_json = json.dumps(SCENE_SCRIPT_OUTPUT_SCHEMA, allow_nan=False)
    whiteboard_schema_json = json.dumps(WHITEBOARD_PLAN_OUTPUT_SCHEMA, allow_nan=False)
    assert "SceneScript" in script_schema_json
    assert "WhiteboardPlan" in whiteboard_schema_json

    assert SCENE_SCRIPT_OUTPUT_SCHEMA["title"] == "SceneScript"
    assert SCENE_SCRIPT_OUTPUT_SCHEMA["additionalProperties"] is False
    assert set(SCENE_SCRIPT_OUTPUT_SCHEMA["required"]) == {
        "scene_id",
        "language",
        "segments",
        "key_points",
    }
    segment_schema = SCENE_SCRIPT_OUTPUT_SCHEMA["properties"]["segments"]["items"]
    assert set(segment_schema["required"]) == {
        "segment_id",
        "text",
        "estimated_duration_seconds",
        "key_point_ids",
    }
    key_point_schema = SCENE_SCRIPT_OUTPUT_SCHEMA["properties"]["key_points"]["items"]
    assert set(key_point_schema["required"]) == {
        "key_point_id",
        "text",
        "source_ids",
    }

    assert WHITEBOARD_PLAN_OUTPUT_SCHEMA["title"] == "WhiteboardPlan"
    assert WHITEBOARD_PLAN_OUTPUT_SCHEMA["additionalProperties"] is False
    assert set(WHITEBOARD_PLAN_OUTPUT_SCHEMA["required"]) == {
        "scene_id",
        "layout",
        "drawing",
        "handwriting",
        "highlights",
        "continuity",
    }
    action_schema = WHITEBOARD_PLAN_OUTPUT_SCHEMA["properties"]["drawing"][
        "properties"
    ]["actions"]["items"]
    assert {"action_id", "action_type", "segment_id", "region_id", "order"} <= set(
        action_schema["required"]
    )
    assert action_schema["properties"]["order"]["minimum"] == 1
    assert action_schema["properties"]["points"]["items"]["additionalProperties"] is False


def test_schema_limits_match_runtime_constants():
    script_properties = SCENE_SCRIPT_OUTPUT_SCHEMA["properties"]
    segment_schema = script_properties["segments"]["items"]
    key_point_schema = script_properties["key_points"]["items"]

    assert script_properties["segments"]["maxItems"] == MAX_SCENE_SEGMENTS
    assert script_properties["key_points"]["maxItems"] == MAX_SCENE_KEY_POINTS
    assert segment_schema["properties"]["text"]["maxLength"] == (
        MAX_NARRATION_SEGMENT_TEXT_LENGTH
    )
    assert segment_schema["properties"]["key_point_ids"]["maxItems"] == (
        MAX_SEGMENT_KEY_POINTS
    )
    assert key_point_schema["properties"]["text"]["maxLength"] == (
        MAX_KEY_POINT_TEXT_LENGTH
    )
    assert key_point_schema["properties"]["source_ids"]["maxItems"] == (
        MAX_KEY_POINT_SOURCE_IDS
    )

    whiteboard_properties = WHITEBOARD_PLAN_OUTPUT_SCHEMA["properties"]
    region_schema = whiteboard_properties["layout"]["properties"]["regions"]["items"]
    action_schema = whiteboard_properties["drawing"]["properties"]["actions"]["items"]
    handwriting_schema = whiteboard_properties["handwriting"]["properties"]["cues"]
    highlight_schema = whiteboard_properties["highlights"]["properties"]["cues"]
    continuity_properties = whiteboard_properties["continuity"]["properties"]

    assert whiteboard_properties["layout"]["properties"]["regions"]["maxItems"] == (
        MAX_BOARD_REGIONS
    )
    assert region_schema["properties"]["width"]["minimum"] == MIN_REGION_DIMENSION
    assert region_schema["properties"]["height"]["minimum"] == MIN_REGION_DIMENSION
    assert whiteboard_properties["drawing"]["properties"]["actions"]["maxItems"] == (
        MAX_DRAWING_ACTIONS
    )
    assert action_schema["properties"]["points"]["maxItems"] == MAX_ACTION_POINTS
    assert action_schema["properties"]["text"]["maxLength"] == MAX_WRITE_TEXT_LENGTH
    conditional_limits = [
        item.get("else", {}).get("properties", {}).get("text", {}).get("maxLength")
        for item in action_schema["allOf"]
    ]
    assert MAX_NON_WRITE_TEXT_LENGTH in conditional_limits
    assert handwriting_schema["maxItems"] == MAX_HANDWRITING_CUES
    assert highlight_schema["maxItems"] == MAX_HIGHLIGHT_CUES
    assert continuity_properties["carry_over_action_ids"]["maxItems"] == (
        MAX_CONTINUITY_IDS
    )
    assert continuity_properties["clear_region_ids"]["maxItems"] == (
        MAX_CONTINUITY_IDS
    )
    assert continuity_properties["notes"]["maxItems"] == MAX_CONTINUITY_NOTES
    assert continuity_properties["notes"]["items"]["maxLength"] == (
        MAX_CONTINUITY_NOTE_LENGTH
    )


def test_schemas_accept_representative_payloads(
    script_payload, whiteboard_payload
):
    jsonschema = pytest.importorskip("jsonschema")

    jsonschema.Draft202012Validator.check_schema(SCENE_SCRIPT_OUTPUT_SCHEMA)
    jsonschema.Draft202012Validator.check_schema(WHITEBOARD_PLAN_OUTPUT_SCHEMA)
    jsonschema.validate(script_payload, SCENE_SCRIPT_OUTPUT_SCHEMA)
    jsonschema.validate(whiteboard_payload, WHITEBOARD_PLAN_OUTPUT_SCHEMA)


def test_schemas_reject_values_rejected_by_runtime(
    script_payload, whiteboard_payload
):
    jsonschema = pytest.importorskip("jsonschema")

    too_many_segments = copy.deepcopy(script_payload)
    too_many_segments["segments"] = [
        dict(too_many_segments["segments"][0], segment_id=f"seg-{index}")
        for index in range(MAX_SCENE_SEGMENTS + 1)
    ]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(too_many_segments, SCENE_SCRIPT_OUTPUT_SCHEMA)

    bad_source_id = copy.deepcopy(script_payload)
    bad_source_id["key_points"][0]["source_ids"] = ["bad id!"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad_source_id, SCENE_SCRIPT_OUTPUT_SCHEMA)

    tiny_region = copy.deepcopy(whiteboard_payload)
    tiny_region["layout"]["regions"][0]["width"] = MIN_REGION_DIMENSION / 2
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(tiny_region, WHITEBOARD_PLAN_OUTPUT_SCHEMA)

    long_non_write_text = copy.deepcopy(whiteboard_payload)
    long_non_write_text["drawing"]["actions"][2]["text"] = (
        "x" * (MAX_NON_WRITE_TEXT_LENGTH + 1)
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(long_non_write_text, WHITEBOARD_PLAN_OUTPUT_SCHEMA)

    write_action_with_points = copy.deepcopy(whiteboard_payload)
    write_action_with_points["drawing"]["actions"][0]["points"] = [
        {"x": 0.1, "y": 0.1},
        {"x": 0.2, "y": 0.2},
    ]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(write_action_with_points, WHITEBOARD_PLAN_OUTPUT_SCHEMA)

    arrow_with_one_point = copy.deepcopy(whiteboard_payload)
    arrow_with_one_point["drawing"]["actions"][2]["points"] = [
        {"x": 0.1, "y": 0.1}
    ]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(arrow_with_one_point, WHITEBOARD_PLAN_OUTPUT_SCHEMA)
