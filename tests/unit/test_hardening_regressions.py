"""Regression tests for scene-planning hardening findings.

Responsibility: pin the confirmed defects fixed during the integration audit:
exact-text preservation, identifier validation, runtime/schema limits, source
subset checks, highlight rules, continuity rules, geometry, and JSON parsing.
Workflow: deterministic unit tests with fake LLM clients only.
Inputs: fixtures from ``tests/conftest.py``.
Outputs: pytest assertions only.
Interacts with: both agents, shared contracts, validation, schemas, and the LLM
boundary.
"""

from __future__ import annotations

import copy

import pytest

from ai_video_generator.agents.scene_planning.errors import AgentOutputError
from ai_video_generator.agents.scene_planning.llm import (
    LLMResponseFormatError,
    parse_json_object,
)
from ai_video_generator.agents.scene_planning.validation import (
    validate_script,
    validate_whiteboard,
)
from ai_video_generator.agents.scripting.script_agent import ScriptAgent
from ai_video_generator.agents.whiteboard.whiteboard_director_agent import (
    WhiteboardDirectorAgent,
)
from ai_video_generator.models.base import ContractError, require_id
from ai_video_generator.models.scene import (
    MAX_SCENE_KEY_POINTS,
    MAX_SCENE_SEGMENTS,
    KeyPoint,
    NarrationSegment,
    SceneScript,
)
from ai_video_generator.models.scene_planning import ScenePlanningResult
from ai_video_generator.models.source import SourceExcerpt
from ai_video_generator.whiteboard.board_layout import (
    MAX_BOARD_REGIONS,
    MIN_REGION_DIMENSION,
    BoardLayout,
    BoardRegion,
)
from ai_video_generator.whiteboard.drawing_instructions import (
    MAX_ACTION_POINTS,
    MAX_DRAWING_ACTIONS,
    MAX_NON_WRITE_TEXT_LENGTH,
    DrawingAction,
    DrawingActionType,
    DrawingPlan,
    Point,
)
from ai_video_generator.whiteboard.handwriting_plan import (
    MAX_HANDWRITING_CUES,
    HandwritingCue,
    HandwritingMode,
    HandwritingPlan,
)
from ai_video_generator.whiteboard.highlighting import (
    MAX_HIGHLIGHT_CUES,
    HighlightCue,
    HighlightPlan,
    HighlightStyle,
)
from ai_video_generator.whiteboard.plan import WhiteboardPlan
from ai_video_generator.whiteboard.visual_continuity import (
    MAX_CONTINUITY_IDS,
    ContinuityMode,
    ContinuityPlan,
)


def _erase_payload(whiteboard_payload):
    payload = copy.deepcopy(whiteboard_payload)
    payload["drawing"]["actions"][2] = {
        "action_id": "act-3",
        "action_type": "erase",
        "segment_id": "seg-2",
        "region_id": "main",
        "order": 3,
        "points": [
            {"x": 0.2, "y": 0.4},
            {"x": 0.5, "y": 0.4},
        ],
    }
    return payload


# ---------------------------------------------------------------------------
# Exact serialization and identifier handling
# ---------------------------------------------------------------------------


def test_from_dict_preserves_educational_text_exactly(script_payload):
    payload = copy.deepcopy(script_payload)
    payload["segments"][0]["text"] = (
        "  Equation:\n    y = 2x + 1\n\n    print(y)  "
    )
    payload["segments"][1]["text"] = "\n\nIndented code:\n    return x\n"
    payload["key_points"][0]["text"] = "  preserve trailing spaces   "

    script = SceneScript.from_dict(payload)

    assert script.segments[0].text == payload["segments"][0]["text"]
    assert script.segments[1].text == payload["segments"][1]["text"]
    assert script.key_points[0].text == payload["key_points"][0]["text"]
    assert SceneScript.from_dict(script.to_dict()) == script


def test_source_excerpt_round_trip_preserves_text_and_section_title():
    excerpt = SourceExcerpt.from_dict(
        {
            "source_id": "src-exact",
            "reference": {
                "document_id": "doc-1",
                "page_number": 4,
                "section_title": "  Section 3.2  ",
            },
            "text": "  x = 1\n\n  y = 2\n",
        }
    )

    assert excerpt.text == "  x = 1\n\n  y = 2\n"
    assert excerpt.reference.section_title == "  Section 3.2  "
    assert SourceExcerpt.from_dict(excerpt.to_dict()) == excerpt


def test_identifiers_are_not_stripped_or_terminated_by_newline():
    with pytest.raises(ContractError):
        require_id("seg-1\n", "segment_id")
    with pytest.raises(ContractError):
        require_id(" seg-1 ", "segment_id")
    with pytest.raises(ContractError):
        NarrationSegment(" seg-1 ", "text", 1.0, ("kp-1",))

    assert require_id("seg-1", "segment_id") == "seg-1"


def test_identifier_lists_are_validated_as_identifiers(script_payload):
    with pytest.raises(ContractError, match="segment.key_point_ids"):
        NarrationSegment("seg-1", "text", 1.0, ("bad id!",))
    with pytest.raises(ContractError, match="key_point.source_ids"):
        KeyPoint("kp-1", "text", ("bad id!",))
    with pytest.raises(ContractError, match="carry_over_action_ids"):
        ContinuityPlan(
            ContinuityMode.CONTINUE_BOARD,
            carry_over_action_ids=("bad id!",),
        )

    invalid = copy.deepcopy(script_payload)
    invalid["segments"][0]["segment_id"] = " seg-1 "
    with pytest.raises(ContractError):
        SceneScript.from_dict(invalid)


# ---------------------------------------------------------------------------
# Runtime limits shared with the JSON Schema
# ---------------------------------------------------------------------------


def test_scene_script_runtime_limits():
    point = KeyPoint("kp-1", "text", ("src-1",))
    segments = tuple(
        NarrationSegment(f"seg-{index}", "text", 1.0, ("kp-1",))
        for index in range(MAX_SCENE_SEGMENTS + 1)
    )
    with pytest.raises(ContractError, match="at most 40 segments"):
        SceneScript("scene-1", "en", segments, (point,))

    points = tuple(
        KeyPoint(f"kp-{index}", "text", ("src-1",))
        for index in range(MAX_SCENE_KEY_POINTS + 1)
    )
    script_segments = tuple(
        NarrationSegment(f"seg-{index}", "text", 1.0, (f"kp-{index}",))
        for index in range(MAX_SCENE_KEY_POINTS + 1)
    )
    with pytest.raises(ContractError, match="at most 30 key points"):
        SceneScript("scene-1", "en", script_segments, points)


def test_whiteboard_collection_runtime_limits():
    regions = tuple(
        BoardRegion(f"r-{index}", "content", 0.0, 0.0, 0.01, 0.01)
        for index in range(MAX_BOARD_REGIONS + 1)
    )
    with pytest.raises(ContractError, match="at most 12 regions"):
        BoardLayout(regions)

    actions = tuple(
        DrawingAction(
            f"act-{index}",
            DrawingActionType.WRITE_TEXT,
            "seg-1",
            "main",
            index + 1,
            text="x",
        )
        for index in range(MAX_DRAWING_ACTIONS + 1)
    )
    with pytest.raises(ContractError, match="at most 80 actions"):
        DrawingPlan(actions)

    cues = tuple(
        HandwritingCue(
            f"hw-{index}", f"act-{index}", HandwritingMode.HANDWRITE, index + 1
        )
        for index in range(MAX_HANDWRITING_CUES + 1)
    )
    with pytest.raises(ContractError, match="at most 80 cues"):
        HandwritingPlan(cues)

    highlights = tuple(
        HighlightCue(
            f"hl-{index}",
            f"act-{index}",
            "seg-1",
            HighlightStyle.MARKER,
            index + 1,
        )
        for index in range(MAX_HIGHLIGHT_CUES + 1)
    )
    with pytest.raises(ContractError, match="at most 80 cues"):
        HighlightPlan(highlights)

    with pytest.raises(ContractError, match="at most 100"):
        ContinuityPlan(
            ContinuityMode.CONTINUE_BOARD,
            carry_over_action_ids=tuple(
                f"act-{index}" for index in range(MAX_CONTINUITY_IDS + 1)
            ),
        )


def test_action_point_and_text_runtime_limits():
    points = tuple(Point(0.1, 0.1) for _ in range(MAX_ACTION_POINTS + 1))
    with pytest.raises(ContractError, match="at most 20 points"):
        DrawingAction(
            "path-1",
            DrawingActionType.DRAW_PATH,
            "seg-1",
            "main",
            1,
            points=points,
        )

    with pytest.raises(ContractError, match=f"at most {MAX_NON_WRITE_TEXT_LENGTH}"):
        DrawingAction(
            "arrow-1",
            DrawingActionType.DRAW_ARROW,
            "seg-1",
            "main",
            1,
            text="x" * (MAX_NON_WRITE_TEXT_LENGTH + 1),
            points=(Point(0.1, 0.1), Point(0.2, 0.2)),
        )

    long_write = DrawingAction(
        "write-1",
        DrawingActionType.WRITE_TEXT,
        "seg-1",
        "main",
        1,
        text="x" * 2_000,
    )
    assert len(long_write.text) == 2_000


def test_region_dimension_minimum():
    with pytest.raises(ContractError, match="at least 0.001"):
        BoardRegion(
            "tiny",
            "content",
            0.0,
            0.0,
            MIN_REGION_DIMENSION / 2,
            0.1,
        )


# ---------------------------------------------------------------------------
# Source provenance and combined result
# ---------------------------------------------------------------------------


def test_scene_planning_result_rejects_source_ids_outside_scene(
    planning_input, script_payload, whiteboard_payload
):
    invalid_script = copy.deepcopy(script_payload)
    invalid_script["key_points"][0]["source_ids"] = ["src-999"]

    with pytest.raises(ContractError, match="not present in the scene"):
        ScenePlanningResult(
            lesson_id=planning_input.lesson_id,
            scene=planning_input.scene,
            script=SceneScript.from_dict(invalid_script),
            whiteboard=WhiteboardPlan.from_dict(whiteboard_payload),
        )


def test_scene_planning_result_rejects_whiteboard_anchor_outside_script(
    planning_input, script_payload, whiteboard_payload
):
    invalid_plan = copy.deepcopy(whiteboard_payload)
    invalid_plan["drawing"]["actions"][0]["segment_id"] = "seg-999"

    with pytest.raises(ContractError, match="unknown narration"):
        ScenePlanningResult(
            lesson_id=planning_input.lesson_id,
            scene=planning_input.scene,
            script=SceneScript.from_dict(script_payload),
            whiteboard=WhiteboardPlan.from_dict(invalid_plan),
        )


def test_scene_planning_result_rejects_scene_id_mismatch(
    planning_input, script_payload, whiteboard_payload
):
    mismatched_script = copy.deepcopy(script_payload)
    mismatched_script["scene_id"] = "scene-999"

    with pytest.raises(ContractError, match="script.scene_id must match"):
        ScenePlanningResult(
            lesson_id=planning_input.lesson_id,
            scene=planning_input.scene,
            script=SceneScript.from_dict(mismatched_script),
            whiteboard=WhiteboardPlan.from_dict(whiteboard_payload),
        )


# ---------------------------------------------------------------------------
# Highlight and continuity semantics
# ---------------------------------------------------------------------------


def test_whiteboard_contract_rejects_highlighting_erase_action(whiteboard_payload):
    payload = _erase_payload(whiteboard_payload)
    payload["highlights"]["cues"][0]["target_action_id"] = "act-3"

    with pytest.raises(ContractError, match="cannot target erase action"):
        WhiteboardPlan.from_dict(payload)


def test_validation_rejects_highlight_before_target_segment(
    planning_input, script_payload, whiteboard_payload
):
    payload = copy.deepcopy(whiteboard_payload)
    # hl-1 targets act-2, which is anchored to seg-2.
    payload["highlights"]["cues"][0]["segment_id"] = "seg-1"

    plan = WhiteboardPlan.from_dict(payload)
    report = validate_whiteboard(
        plan, SceneScript.from_dict(script_payload), planning_input
    )

    assert not report.is_valid
    assert "whiteboard.highlight_before_target" in {issue.code for issue in report.errors}


def test_validation_rejects_carry_over_of_erase_action(
    planning_input, script_payload, whiteboard_payload
):
    previous_payload = _erase_payload(whiteboard_payload)
    previous_payload["scene_id"] = "scene-000"
    previous_plan = WhiteboardPlan.from_dict(previous_payload)

    current_payload = copy.deepcopy(whiteboard_payload)
    current_payload["continuity"] = {
        "mode": "continue_board",
        "carry_over_action_ids": ["act-3"],
        "clear_region_ids": [],
        "notes": [],
    }
    current_plan = WhiteboardPlan.from_dict(current_payload)

    report = validate_whiteboard(
        current_plan,
        SceneScript.from_dict(script_payload),
        planning_input,
        previous_plan,
    )

    assert not report.is_valid
    assert "whiteboard.carry_over_erase" in {issue.code for issue in report.errors}


def test_validation_rejects_carry_over_from_cleared_region(
    planning_input, script_payload, whiteboard_payload
):
    previous_payload = copy.deepcopy(whiteboard_payload)
    previous_payload["scene_id"] = "scene-000"
    previous_plan = WhiteboardPlan.from_dict(previous_payload)

    current_payload = copy.deepcopy(whiteboard_payload)
    current_payload["continuity"] = {
        "mode": "clear_regions",
        "carry_over_action_ids": ["act-1"],
        "clear_region_ids": ["main"],
        "notes": [],
    }
    current_plan = WhiteboardPlan.from_dict(current_payload)

    report = validate_whiteboard(
        current_plan,
        SceneScript.from_dict(script_payload),
        planning_input,
        previous_plan,
    )

    assert not report.is_valid
    assert "whiteboard.carry_over_cleared_region" in {
        issue.code for issue in report.errors
    }


# ---------------------------------------------------------------------------
# Geometry and ordering
# ---------------------------------------------------------------------------


def test_whiteboard_contract_rejects_points_outside_referenced_region(
    whiteboard_payload,
):
    payload = copy.deepcopy(whiteboard_payload)
    payload["drawing"]["actions"][2]["points"] = [
        {"x": 0.9, "y": 0.9},
        {"x": 0.95, "y": 0.95},
    ]

    with pytest.raises(ContractError, match="lies outside its region_id"):
        WhiteboardPlan.from_dict(payload)


def test_whiteboard_contract_rejects_handwriting_out_of_drawing_order(
    whiteboard_payload,
):
    payload = copy.deepcopy(whiteboard_payload)
    payload["handwriting"]["cues"][0]["action_id"] = "act-2"
    payload["handwriting"]["cues"][1]["action_id"] = "act-1"

    with pytest.raises(ContractError, match="action_order"):
        WhiteboardPlan.from_dict(payload)


def test_drawing_action_type_payload_rules():
    with pytest.raises(ContractError, match="action.text must be a string"):
        DrawingAction(
            "write-1",
            DrawingActionType.WRITE_TEXT,
            "seg-1",
            "main",
            1,
        )

    with pytest.raises(ContractError, match="must not define points"):
        DrawingAction(
            "write-1",
            DrawingActionType.WRITE_TEXT,
            "seg-1",
            "main",
            1,
            text="x",
            points=(Point(0.1, 0.1),),
        )

    with pytest.raises(ContractError, match="requires exactly 2 points"):
        DrawingAction(
            "arrow-1",
            DrawingActionType.DRAW_ARROW,
            "seg-1",
            "main",
            1,
            points=(Point(0.1, 0.1),),
        )

    with pytest.raises(ContractError, match="requires at least 2 points"):
        DrawingAction(
            "path-1",
            DrawingActionType.DRAW_PATH,
            "seg-1",
            "main",
            1,
            points=(Point(0.1, 0.1),),
        )

    with pytest.raises(ContractError, match="action.action_type"):
        DrawingAction(
            "bad-1",
            "not-an-action-type",
            "seg-1",
            "main",
            1,
            text="x",
        )


# ---------------------------------------------------------------------------
# Narrated timing is anchored to segment IDs; duration fields are estimates
# ---------------------------------------------------------------------------


def test_duration_estimates_are_advisory_and_whiteboard_anchors_are_segments(
    planning_input, script_payload, whiteboard_payload
):
    payload = copy.deepcopy(script_payload)
    for segment in payload["segments"]:
        segment["estimated_duration_seconds"] = 1.0
    script = SceneScript.from_dict(payload)

    report = validate_script(script, planning_input)

    assert script.estimated_duration_seconds == 3.0
    assert report.is_valid
    assert "script.duration_deviation" in {issue.code for issue in report.warnings}

    plan = WhiteboardPlan.from_dict(whiteboard_payload)
    segment_ids = {segment.segment_id for segment in script.segments}
    assert all(action.segment_id in segment_ids for action in plan.drawing.actions)
    assert all(cue.segment_id in segment_ids for cue in plan.highlights.cues)
    assert all(cue.action_id in plan.drawing.action_ids for cue in plan.handwriting.cues)

    def key_names(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from key_names(child)
        elif isinstance(value, list):
            for child in value:
                yield from key_names(child)

    serialized_keys = set(key_names(plan.to_dict()))
    assert not {
        "timestamp",
        "start_time",
        "end_time",
        "absolute_time",
    } & serialized_keys


# ---------------------------------------------------------------------------
# LLM response parsing
# ---------------------------------------------------------------------------


def test_parse_json_object_rejects_unclosed_fence_and_duplicate_keys():
    with pytest.raises(LLMResponseFormatError, match="unclosed code fence"):
        parse_json_object('```json\n{"a": 1}')
    with pytest.raises(LLMResponseFormatError, match="duplicate JSON key 'a'"):
        parse_json_object('{"a": 1, "a": 2}')
    with pytest.raises(LLMResponseFormatError, match="duplicate JSON key 'b'"):
        parse_json_object('{"outer": {"b": 1, "b": 2}}')
    with pytest.raises(LLMResponseFormatError, match="non-finite"):
        parse_json_object('{"a": NaN}')
    with pytest.raises(LLMResponseFormatError, match="outside the JSON code fence"):
        parse_json_object('```json\n{"a": 1}\n```\ntrailing')


def test_parse_json_object_accepts_one_complete_fence():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}


# ---------------------------------------------------------------------------
# Agent-level regression coverage
# ---------------------------------------------------------------------------


def test_script_agent_preserves_exact_segment_text(
    planning_input, script_payload, fake_llm_factory
):
    payload = copy.deepcopy(script_payload)
    payload["segments"][0]["text"] = "  keep leading\n    and indentation  "
    agent = ScriptAgent(fake_llm_factory(payload))

    script = agent.generate(planning_input)

    assert script.segments[0].text == payload["segments"][0]["text"]


def test_script_agent_rejects_padded_identifier(
    planning_input, script_payload, fake_llm_factory
):
    payload = copy.deepcopy(script_payload)
    payload["segments"][0]["segment_id"] = " seg-1 "
    agent = ScriptAgent(fake_llm_factory(payload))

    with pytest.raises(AgentOutputError, match="contract validation failed"):
        agent.generate(planning_input)


def test_whiteboard_agent_rejects_highlight_before_target(
    planning_input, script_payload, whiteboard_payload, fake_llm_factory
):
    payload = copy.deepcopy(whiteboard_payload)
    payload["highlights"]["cues"][0]["segment_id"] = "seg-1"
    agent = WhiteboardDirectorAgent(fake_llm_factory(payload))

    with pytest.raises(AgentOutputError) as exc_info:
        agent.generate_for_scene(planning_input, SceneScript.from_dict(script_payload))

    assert "whiteboard.highlight_before_target" in "\n".join(exc_info.value.details)


def test_whiteboard_agent_rejects_carry_over_of_erase_action(
    planning_input, script_payload, whiteboard_payload, fake_llm_factory
):
    previous_payload = _erase_payload(whiteboard_payload)
    previous_payload["scene_id"] = "scene-000"
    previous_plan = WhiteboardPlan.from_dict(previous_payload)

    current_payload = copy.deepcopy(whiteboard_payload)
    current_payload["continuity"] = {
        "mode": "continue_board",
        "carry_over_action_ids": ["act-3"],
        "clear_region_ids": [],
        "notes": [],
    }
    agent = WhiteboardDirectorAgent(fake_llm_factory(current_payload))

    with pytest.raises(AgentOutputError) as exc_info:
        agent.generate_for_scene(
            planning_input,
            SceneScript.from_dict(script_payload),
            previous_plan=previous_plan,
        )

    assert "whiteboard.carry_over_erase" in "\n".join(exc_info.value.details)
