"""Deterministic end-to-end test for the planned-scene pipeline.

Responsibility: exercise the implemented sequence of Script Agent, Whiteboard
Director Agent, Example & Visual Agent, ScenePackager, and ScenePromptBuilder
with one fake LLM client, and verify that identity, ordering, source references,
and exact text survive the handoffs.
Workflow: deterministic integration test; no provider, TTS, network, renderer, or
paid API access.
Inputs: shared fixtures from ``tests/conftest.py`` and small local visual
response payloads.
Outputs: pytest assertions only.
Interacts with: ``agents.scripting``, ``agents.whiteboard``,
``agents.visual_design``, ``agents.scene_packaging``, ``models.scene_planning``,
``video.scene_contract``, and ``video.scene_timing``.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from ai_video_generator.agents.scene_packaging import (
    PromptTimingBasis,
    SceneDurationError,
    ScenePackager,
    ScenePackagingInputError,
    ScenePromptBuilder,
)
from ai_video_generator.agents.scene_planning.errors import AgentOutputError
from ai_video_generator.agents.scripting import ScriptAgent
from ai_video_generator.agents.visual_design import VisualDesignAgent
from ai_video_generator.agents.whiteboard import WhiteboardDirectorAgent
from ai_video_generator.models.scene_planning import ScenePlanningResult
from ai_video_generator.video.scene_contract import ScenePackage
from ai_video_generator.video.scene_timing import DurationLimits


def _visual_item_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "visual_id": "vis-layers",
        "purpose": "explain_concept",
        "kind": "diagram",
        "description": "Show each layer passing information to the next group.",
        "segment_ids": ["seg-1"],
        "key_point_ids": ["kp-1"],
        "source_ids": ["src-1"],
        "exact_text": ["Layer 1 -> Layer 2", "E = mc^2", "print('hello')"],
        "accessibility_notes": ["Describe the flow direction aloud."],
    }
    payload.update(overrides)
    return payload


def _visual_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "scene_id": "scene-001",
        "items": [_visual_item_payload()],
    }
    payload.update(overrides)
    return payload


def _run_pipeline(
    planning_input,
    script_payload,
    whiteboard_payload,
    visual_payload,
    fake_llm_factory,
):
    llm = fake_llm_factory(
        json.dumps(script_payload),
        json.dumps(whiteboard_payload),
        json.dumps(visual_payload),
    )
    script = ScriptAgent(llm).generate(planning_input)
    whiteboard = WhiteboardDirectorAgent(llm).generate_for_scene(
        planning_input, script
    )
    planning_result = ScenePlanningResult(
        lesson_id=planning_input.lesson_id,
        scene=planning_input.scene,
        script=script,
        whiteboard=whiteboard,
    )
    visual_result = VisualDesignAgent(llm).generate_for_scene(
        planning_input, script, whiteboard=whiteboard
    )
    return llm, planning_result, visual_result


def test_visual_plan_packages_into_prompt_with_identity_and_text_preserved(
    planning_input, script_payload, whiteboard_payload, fake_llm_factory
):
    llm, planning_result, visual_result = _run_pipeline(
        planning_input,
        script_payload,
        whiteboard_payload,
        _visual_payload(),
        fake_llm_factory,
    )

    package = ScenePackager().package(
        planning_result,
        visual_result,
        DurationLimits(minimum_seconds=25.0, maximum_seconds=35.0),
    )
    prompt = ScenePromptBuilder().build(package)

    assert len(llm.requests) == 3
    assert llm.requests[2].response_schema_name == "VisualPlan"

    assert package.lesson_id == planning_input.lesson_id
    assert package.scene_id == planning_input.scene.scene_id
    assert package.script == planning_result.script
    assert package.whiteboard == planning_result.whiteboard
    assert package.visual == visual_result.plan
    assert package.source_excerpts == planning_input.scene.source_excerpts
    assert tuple(entry.segment_id for entry in package.timeline.segments) == (
        "seg-1",
        "seg-2",
        "seg-3",
    )
    assert package.timeline.total_duration_seconds == pytest.approx(30.0)
    assert package.timeline.findings == ()

    assert prompt.lesson_id == package.lesson_id
    assert prompt.scene_id == package.scene_id
    assert prompt.script == package.script
    assert prompt.visual == package.visual
    assert prompt.whiteboard == package.whiteboard
    assert prompt.source_excerpts == package.source_excerpts

    item = prompt.visual.items[0]
    assert item.visual_id == "vis-layers"
    assert item.segment_ids == ("seg-1",)
    assert item.key_point_ids == ("kp-1",)
    assert item.source_ids == ("src-1",)
    assert item.exact_text == (
        "Layer 1 -> Layer 2",
        "E = mc^2",
        "print('hello')",
    )
    assert prompt.visual.items[0].accessibility_notes == (
        "Describe the flow direction aloud.",
    )
    assert prompt.script.key_points[0].source_ids == ("src-1",)
    assert prompt.script.segments[0].text == (
        planning_result.script.segments[0].text
    )

    assert prompt.timing.basis is PromptTimingBasis.PLANNING_ESTIMATE
    assert prompt.timing.timeline == package.timeline
    assert prompt.timing.estimated_total_duration_seconds == pytest.approx(30.0)

    assert ScenePackage.from_dict(package.to_dict()) == package
    assert prompt.from_dict(prompt.to_dict()) == prompt


def test_visual_plan_with_unknown_reference_blocks_before_packaging(
    planning_input, script_payload, whiteboard_payload, fake_llm_factory
):
    with pytest.raises(AgentOutputError) as excinfo:
        _run_pipeline(
            planning_input,
            script_payload,
            whiteboard_payload,
            _visual_payload(
                items=[_visual_item_payload(segment_ids=["seg-404"])]
            ),
            fake_llm_factory,
        )

    assert "visual.unknown_segment" in "\n".join(excinfo.value.details)


def test_packager_rejects_duration_bound_violation(
    planning_input, script_payload, whiteboard_payload, fake_llm_factory
):
    _, planning_result, visual_result = _run_pipeline(
        planning_input,
        script_payload,
        whiteboard_payload,
        _visual_payload(),
        fake_llm_factory,
    )

    with pytest.raises(SceneDurationError) as excinfo:
        ScenePackager().package(
            planning_result,
            visual_result,
            DurationLimits(minimum_seconds=25.0, maximum_seconds=28.0),
        )

    assert [finding.code for finding in excinfo.value.findings] == [
        "timing.total_above_maximum",
        "timing.target_above_maximum",
    ]


def test_packager_preserves_target_mismatch_as_advisory(
    planning_input, script_payload, whiteboard_payload, fake_llm_factory
):
    _, planning_result, visual_result = _run_pipeline(
        planning_input,
        script_payload,
        whiteboard_payload,
        _visual_payload(),
        fake_llm_factory,
    )
    shortened_target = dataclasses.replace(
        planning_result.scene, target_duration_seconds=25.0
    )
    planning_result = ScenePlanningResult(
        lesson_id=planning_result.lesson_id,
        scene=shortened_target,
        script=planning_result.script,
        whiteboard=planning_result.whiteboard,
    )

    package = ScenePackager().package(
        planning_result,
        visual_result,
        DurationLimits(
            minimum_seconds=20.0,
            maximum_seconds=35.0,
            tolerance_seconds=0.0,
        ),
    )

    assert [finding.code for finding in package.timeline.findings] == [
        "timing.target_mismatch"
    ]
    assert package.timeline.findings[0].severity.value == "warning"
    assert package.timeline.total_duration_seconds == pytest.approx(30.0)
    assert package.timeline.target_duration_seconds == pytest.approx(25.0)

    prompt = ScenePromptBuilder().build(package)
    assert prompt.timing.timeline.findings == package.timeline.findings
    assert prompt.timing.basis is PromptTimingBasis.PLANNING_ESTIMATE


def test_packager_rejects_mistyped_input(
    planning_input, script_payload, whiteboard_payload, fake_llm_factory
):
    _, _, visual_result = _run_pipeline(
        planning_input,
        script_payload,
        whiteboard_payload,
        _visual_payload(),
        fake_llm_factory,
    )

    with pytest.raises(
        ScenePackagingInputError,
        match="planning_result must be a ScenePlanningResult",
    ):
        ScenePackager().package(
            {},
            visual_result,
            DurationLimits(minimum_seconds=1.0, maximum_seconds=60.0),
        )
