"""Tests for the planning-to-runtime scene adapter.

Responsibility: verify the contract bridge from ``ScenePackage`` to the runtime
``SceneSpec``/``SceneSet`` without media work.
Workflow: unit tests over deterministic planning fixtures.
Inputs: conftest planning script, whiteboard, and visual plans.
Outputs: assertions on narration, provenance, text, ordering, and typed errors.
Interacts with: ``agents.orchestration.runtime_adapter``, ``video.scene_contract``,
and ``video.generation_contract``.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any

import pytest

from ai_video_generator.agents.orchestration.runtime_adapter import (
    RuntimeAdapterError,
    RuntimeSceneOptions,
    SceneRuntimeAdapter,
)
from ai_video_generator.models.lesson_plan import LessonPlan
from ai_video_generator.models.scene import SceneScript
from ai_video_generator.models.visual_plan import VisualPlan
from ai_video_generator.video.scene_contract import ScenePackage
from ai_video_generator.video.scene_timing import (
    DurationLimits,
    build_scene_timeline,
)
from ai_video_generator.whiteboard.plan import WhiteboardPlan

LIMITS = DurationLimits(minimum_seconds=5.0, maximum_seconds=120.0)


def _script(payload: dict[str, Any]) -> SceneScript:
    return SceneScript.from_dict(copy.deepcopy(payload))


def _visual(scene_id: str = "scene-001", *exact_text: str) -> VisualPlan:
    item: dict[str, Any] = {
        "visual_id": "vis-1",
        "purpose": "explain_concept",
        "kind": "diagram",
        "description": "Show how layers pass information forward.",
        "segment_ids": ["seg-1"],
        "key_point_ids": ["kp-1"],
        "source_ids": ["src-1"],
    }
    if exact_text:
        item["exact_text"] = list(exact_text)
    return VisualPlan.from_dict({"scene_id": scene_id, "items": [item]})


def _package(
    planning_input,
    script: SceneScript,
    whiteboard: WhiteboardPlan,
    visual: VisualPlan | None = None,
) -> ScenePackage:
    script = copy.deepcopy(script)
    timeline = build_scene_timeline(
        script,
        LIMITS,
        target_duration_seconds=planning_input.scene.target_duration_seconds,
    )
    return ScenePackage(
        lesson_id=planning_input.lesson_id,
        scene_id=script.scene_id,
        script=script,
        visual=visual or _visual(script.scene_id, "E = mc^2"),
        whiteboard=whiteboard,
        timeline=timeline,
        source_excerpts=planning_input.scene.source_excerpts,
    )


def _lesson_plan(planning_input) -> LessonPlan:
    return LessonPlan(
        lesson_id=planning_input.lesson_id,
        title="Neural Networks",
        selected_pages=planning_input.selected_pages,
        preferences=planning_input.preferences,
        scenes=(planning_input.scene,),
    )


def test_scene_adaptation_preserves_narration_and_provenance(
    planning_input, script_payload, whiteboard_payload
):
    script = _script(script_payload)
    whiteboard = WhiteboardPlan.from_dict(copy.deepcopy(whiteboard_payload))
    package = _package(planning_input, script, whiteboard)

    scene = SceneRuntimeAdapter().adapt_scene(
        package, planning_input.scene, scene_index=0
    )

    assert scene.scene_id == "scene-001"
    assert scene.scene_index == 0
    assert scene.title == planning_input.scene.title
    assert scene.learning_objective == planning_input.scene.objective
    assert scene.narration == script.narration
    assert scene.target_duration_seconds == pytest.approx(30.0)
    # The source section title equals the scene title and is not duplicated in
    # the assembly overlay label.
    assert scene.section_title is None
    assert len(scene.source_references) == 2
    first = scene.source_references[0]
    assert first.document_id == "book-1"
    assert first.page_numbers == [12]
    assert first.reference == "Neural Networks"
    assert first.excerpt == planning_input.scene.source_excerpts[0].text
    assert scene.visual.whiteboard_style == "clean_whiteboard"
    assert scene.status.value == "pending"


def test_scene_adaptation_builds_visual_text_from_whiteboard_and_visual_plan(
    planning_input, script_payload, whiteboard_payload
):
    whiteboard = WhiteboardPlan.from_dict(copy.deepcopy(whiteboard_payload))
    package = _package(
        planning_input,
        _script(script_payload),
        whiteboard,
        _visual("scene-001", "E = mc^2", "Neural network = connected layers"),
    )

    scene = SceneRuntimeAdapter().adapt_scene(
        package, planning_input.scene, scene_index=0
    )

    texts = [element.text for element in scene.visual.text_elements]
    assert texts == [
        "Neural network = connected layers",
        "Training adjusts weights",
        "Predict the next play",
        "E = mc^2",
    ]
    by_text = {element.text: element for element in scene.visual.text_elements}
    assert by_text["Training adjusts weights"].emphasis is True
    assert by_text["Neural network = connected layers"].emphasis is False
    assert any(
        step.startswith("3. draw_arrow") for step in scene.visual.drawing_steps
    )
    assert scene.visual.instructions[0].startswith("Show how layers")
    assert any(
        "reveal one idea" in instruction.lower()
        for instruction in scene.visual.instructions
    )
    assert scene.expected_visible_texts[0] == "Neural Networks"


def test_lesson_adaptation_assigns_contiguous_indices_and_lesson_metadata(
    planning_input, script_payload, whiteboard_payload
):
    lesson_plan = _lesson_plan(planning_input)
    package = _package(
        planning_input,
        _script(script_payload),
        WhiteboardPlan.from_dict(copy.deepcopy(whiteboard_payload)),
    )

    scene_set = SceneRuntimeAdapter().adapt_lesson(
        [package],
        lesson_plan,
        options=RuntimeSceneOptions(whiteboard_style="marker_board"),
    )

    assert scene_set.project_id == "lesson-001"
    assert scene_set.course_title == "Neural Networks"
    assert scene_set.expected_style == "marker_board"
    assert [scene.scene_index for scene in scene_set.scenes] == [0]
    assert scene_set.scenes[0].visual.whiteboard_style == "marker_board"


def test_lesson_adaptation_requires_a_package_for_every_scene(
    planning_input, script_payload, whiteboard_payload
):
    lesson_plan = _lesson_plan(planning_input)
    package = _package(
        planning_input,
        _script(script_payload),
        WhiteboardPlan.from_dict(copy.deepcopy(whiteboard_payload)),
    )

    with pytest.raises(RuntimeAdapterError, match="no scene package"):
        SceneRuntimeAdapter().adapt_lesson(
            [], lesson_plan
        )

    script_data = copy.deepcopy(script_payload)
    script_data["scene_id"] = "scene-002"
    whiteboard_data = copy.deepcopy(whiteboard_payload)
    whiteboard_data["scene_id"] = "scene-002"
    script = SceneScript.from_dict(script_data)
    whiteboard = WhiteboardPlan.from_dict(whiteboard_data)
    extra_timeline = build_scene_timeline(
        script, LIMITS, target_duration_seconds=planning_input.scene.target_duration_seconds
    )
    extra = ScenePackage(
        lesson_id=planning_input.lesson_id,
        scene_id="scene-002",
        script=script,
        visual=_visual("scene-002"),
        whiteboard=whiteboard,
        timeline=extra_timeline,
        source_excerpts=planning_input.scene.source_excerpts,
    )
    with pytest.raises(RuntimeAdapterError, match="do not belong"):
        SceneRuntimeAdapter().adapt_lesson(
            [package, extra], lesson_plan
        )


def test_unsafe_runtime_identifier_is_rejected_with_a_clear_error(
    planning_input, script_payload, whiteboard_payload
):
    unsafe_id = "scene:001"
    brief = planning_input.scene
    unsafe_brief = replace(brief, scene_id=unsafe_id)

    script_data = copy.deepcopy(script_payload)
    script_data["scene_id"] = unsafe_id
    whiteboard_data = copy.deepcopy(whiteboard_payload)
    whiteboard_data["scene_id"] = unsafe_id
    script = SceneScript.from_dict(script_data)
    whiteboard = WhiteboardPlan.from_dict(whiteboard_data)

    timeline = build_scene_timeline(
        script, LIMITS, target_duration_seconds=brief.target_duration_seconds
    )
    package = ScenePackage(
        lesson_id=planning_input.lesson_id,
        scene_id=unsafe_id,
        script=script,
        visual=_visual(unsafe_id),
        whiteboard=whiteboard,
        timeline=timeline,
        source_excerpts=brief.source_excerpts,
    )

    with pytest.raises(RuntimeAdapterError, match="runtime-safe"):
        SceneRuntimeAdapter().adapt_scene(package, unsafe_brief, scene_index=0)
