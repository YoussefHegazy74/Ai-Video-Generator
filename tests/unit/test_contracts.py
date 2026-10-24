"""Contract-level tests for scene-planning models and serialization.

Responsibility: verify source provenance, cross-reference validation, whiteboard
structural rules, and JSON round-trips.
Workflow: unit tests for the shared contracts used by both agents.
Inputs: deterministic fixtures from ``tests/conftest.py``.
Outputs: pytest assertions only.
Interacts with: ``models``, ``whiteboard`` contracts, and validation.
"""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from ai_video_generator.models.base import ContractError
from ai_video_generator.models.lesson_plan import LessonPlan, SceneBrief
from ai_video_generator.models.scene import ScenePlanningInput, SceneScript
from ai_video_generator.models.scene_planning import ScenePlanningResult
from ai_video_generator.models.source import (
    PageRange,
    SourceExcerpt,
    SourceReference,
)
from ai_video_generator.whiteboard.board_layout import BoardLayout, BoardRegion
from ai_video_generator.whiteboard.plan import WhiteboardDirectionInput, WhiteboardPlan


def test_page_range_and_source_reference_round_trip():
    page_range = PageRange(10, 20)
    assert page_range.contains(10)
    assert page_range.contains(20)
    assert not page_range.contains(21)
    assert page_range.label == "10-20"
    assert PageRange.from_dict(page_range.to_dict()) == page_range

    reference = SourceReference("book-1", 12, "Neural Networks", "figure-2")
    assert reference.identity == ("book-1", 12, "Neural Networks", "figure-2")
    assert SourceReference.from_dict(reference.to_dict()) == reference


def test_source_excerpt_outside_selected_pages_is_rejected(planning_input):
    scene = planning_input.scene
    outside = SourceExcerpt(
        source_id="src-outside",
        reference=SourceReference("book-1", 99, "Outside"),
        text="This page was not selected.",
    )
    invalid_scene = SceneBrief(
        scene_id=scene.scene_id,
        sequence=scene.sequence,
        title=scene.title,
        objective=scene.objective,
        topics=scene.topics,
        target_duration_seconds=scene.target_duration_seconds,
        source_excerpts=scene.source_excerpts + (outside,),
    )
    with pytest.raises(ContractError, match="outside the selected pages"):
        ScenePlanningInput(
            lesson_id=planning_input.lesson_id,
            scene=invalid_scene,
            preferences=planning_input.preferences,
            selected_pages=planning_input.selected_pages,
        )


def test_scene_script_rejects_unknown_key_point_reference(script_payload):
    invalid = copy.deepcopy(script_payload)
    invalid["segments"][0]["key_point_ids"] = ["kp-missing"]
    with pytest.raises(ContractError, match="unknown key_point_id"):
        SceneScript.from_dict(invalid)


def test_scene_script_round_trip(script_payload):
    script = SceneScript.from_dict(script_payload)
    assert script.scene_id == "scene-001"
    assert script.estimated_duration_seconds == 30.0
    assert script.word_count > 0
    assert script.cited_source_ids == ("src-1", "src-2")
    assert SceneScript.from_dict(script.to_dict()) == script


def test_whiteboard_plan_round_trip(whiteboard_payload):
    plan = WhiteboardPlan.from_dict(whiteboard_payload)
    assert plan.scene_id == "scene-001"
    assert plan.segment_anchors == ("seg-1", "seg-2", "seg-3")
    assert plan.element_ids == ("act-1", "act-2", "act-3", "act-4")
    assert WhiteboardPlan.from_dict(plan.to_dict()) == plan


def test_whiteboard_plan_rejects_unknown_region(whiteboard_payload):
    invalid = copy.deepcopy(whiteboard_payload)
    invalid["drawing"]["actions"][0]["region_id"] = "region-missing"
    with pytest.raises(ContractError, match="unknown region_id"):
        WhiteboardPlan.from_dict(invalid)


def test_continuity_requires_carry_over_for_continue_mode(whiteboard_payload):
    invalid = copy.deepcopy(whiteboard_payload)
    invalid["continuity"] = {
        "mode": "continue_board",
        "carry_over_action_ids": [],
        "clear_region_ids": [],
        "notes": [],
    }
    with pytest.raises(ContractError, match="carry over at least one action"):
        WhiteboardPlan.from_dict(invalid)


def test_equation_handwriting_mode_must_match_action(whiteboard_payload):
    invalid = copy.deepcopy(whiteboard_payload)
    invalid["drawing"]["actions"][0]["action_type"] = "write_equation"
    with pytest.raises(ContractError, match="equation mode"):
        WhiteboardPlan.from_dict(invalid)


def test_layout_overlap_detection():
    layout = BoardLayout(
        regions=(
            BoardRegion("a", "content", 0.0, 0.0, 0.5, 0.5),
            BoardRegion("b", "example", 0.4, 0.4, 0.5, 0.5),
        )
    )
    assert layout.overlapping_pairs() == (("a", "b"),)


def test_scene_planning_result_round_trip(
    planning_input, script_payload, whiteboard_payload
):
    script = SceneScript.from_dict(script_payload)
    plan = WhiteboardPlan.from_dict(whiteboard_payload)
    result = ScenePlanningResult(
        lesson_id=planning_input.lesson_id,
        scene=planning_input.scene,
        script=script,
        whiteboard=plan,
    )
    assert result.scene.source_excerpts[0].page_number == 12
    assert result.scene.source_excerpts[1].page_number == 23
    assert ScenePlanningResult.from_dict(result.to_dict()) == result


def test_lesson_plan_preserves_selection_and_scene_order(planning_input):
    lesson = LessonPlan(
        lesson_id=planning_input.lesson_id,
        title="Neural Networks Lesson",
        selected_pages=planning_input.selected_pages,
        preferences=planning_input.preferences,
        scenes=(planning_input.scene,),
    )
    assert lesson.scene_by_id("scene-001") == planning_input.scene
    assert lesson.selected_pages == (PageRange(10, 20), PageRange(22, 24))
    assert LessonPlan.from_dict(lesson.to_dict()) == lesson


def test_lesson_plan_rejects_scene_sequence_out_of_order(planning_input):
    first = replace(planning_input.scene, sequence=2)
    second = replace(planning_input.scene, scene_id="scene-002", sequence=1)

    with pytest.raises(ContractError, match="lesson.scenes.sequence"):
        LessonPlan(
            lesson_id=planning_input.lesson_id,
            title="Out-of-order lesson",
            selected_pages=planning_input.selected_pages,
            preferences=planning_input.preferences,
            scenes=(first, second),
        )


def test_whiteboard_direction_input_rejects_scene_id_mismatch(
    planning_input, script_payload
):
    script = replace(SceneScript.from_dict(script_payload), scene_id="scene-999")

    with pytest.raises(
        ContractError,
        match="direction_input.script.scene_id must match",
    ):
        WhiteboardDirectionInput(planning_input=planning_input, script=script)
