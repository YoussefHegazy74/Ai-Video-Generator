"""Unit tests for deterministic, provider-neutral scene prompt data.

Responsibility: verify that a canonical ``ScenePackage`` is projected into
structured prompt data without rewriting, inventing, or dropping content, and
that planning timing is labeled separately from real audio timing.
Workflow: deterministic unit tests using the shared scene fixtures; no LLM,
provider, TTS, network, or rendering calls.
Inputs: ``ScenePackage`` values built from ``tests/conftest.py`` fixtures.
Outputs: pytest assertions only.
Interacts with: ``agents.scene_packaging.scene_prompt_builder``, the canonical
scene package, and the existing script/visual/whiteboard/source contracts.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import socket

import pytest

from ai_video_generator.agents.scene_packaging import (
    PromptTimingBasis,
    SCENE_PROMPT_SCHEMA_VERSION,
    SCENE_PROMPT_TIMING_NOTICE,
    SUPPORTED_SCENE_PROMPT_SCHEMA_VERSIONS,
    ScenePackagingInputError,
    ScenePrompt,
    ScenePromptBuilder,
    ScenePromptTiming,
    build_scene_prompt,
)
from ai_video_generator.models.base import ContractError
from ai_video_generator.models.scene import SceneScript
from ai_video_generator.models.source import SourceExcerpt
from ai_video_generator.models.visual_plan import VisualItem, VisualPlan
from ai_video_generator.video.scene_contract import ScenePackage
from ai_video_generator.video.scene_timing import (
    DurationLimits,
    SceneTimeline,
    build_scene_timeline,
)
from ai_video_generator.whiteboard.plan import WhiteboardPlan


def _script(script_payload: dict[str, object]) -> SceneScript:
    return SceneScript.from_dict(copy.deepcopy(script_payload))


def _whiteboard(whiteboard_payload: dict[str, object]) -> WhiteboardPlan:
    return WhiteboardPlan.from_dict(copy.deepcopy(whiteboard_payload))


def _timeline(script: SceneScript) -> SceneTimeline:
    return build_scene_timeline(
        script,
        DurationLimits(1.0, 120.0),
        target_duration_seconds=30.0,
    )


def _visual_item(**overrides: object) -> VisualItem:
    payload: dict[str, object] = {
        "visual_id": "vis-1",
        "purpose": "explain_concept",
        "kind": "diagram",
        "description": "Show how layers pass information forward.",
        "segment_ids": ["seg-1"],
        "key_point_ids": ["kp-1"],
        "source_ids": ["src-1"],
    }
    payload.update(overrides)
    return VisualItem.from_dict(payload)


def _default_visual_items() -> tuple[VisualItem, ...]:
    return (
        _visual_item(),
        _visual_item(
            visual_id="vis-2",
            purpose="illustrate_example",
            kind="real_world_example",
            description="Use a football practice example for training.",
            segment_ids=["seg-2", "seg-3"],
            key_point_ids=["kp-2"],
            source_ids=["src-2"],
        ),
    )


def _visual(
    *,
    scene_id: str = "scene-001",
    items: tuple[VisualItem, ...] | None = None,
) -> VisualPlan:
    return VisualPlan(
        scene_id=scene_id,
        items=_default_visual_items() if items is None else items,
    )


def _package(
    planning_input,
    script_payload: dict[str, object],
    whiteboard_payload: dict[str, object],
    *,
    script: SceneScript | None = None,
    visual: VisualPlan | None = None,
    whiteboard: WhiteboardPlan | None = None,
    timeline: SceneTimeline | None = None,
    source_excerpts: tuple[SourceExcerpt, ...] | None = None,
) -> ScenePackage:
    resolved_script = (
        _script(script_payload) if script is None else script
    )
    resolved_whiteboard = (
        _whiteboard(whiteboard_payload)
        if whiteboard is None
        else whiteboard
    )
    resolved_visual = _visual() if visual is None else visual
    resolved_timeline = (
        _timeline(resolved_script) if timeline is None else timeline
    )
    resolved_sources = (
        planning_input.scene.source_excerpts
        if source_excerpts is None
        else source_excerpts
    )
    return ScenePackage(
        lesson_id=planning_input.lesson_id,
        scene_id=planning_input.scene.scene_id,
        script=resolved_script,
        visual=resolved_visual,
        whiteboard=resolved_whiteboard,
        timeline=resolved_timeline,
        source_excerpts=resolved_sources,
    )


def test_scene_prompt_builder_is_deterministic(
    planning_input, script_payload, whiteboard_payload
):
    package = _package(
        planning_input, script_payload, whiteboard_payload
    )
    builder = ScenePromptBuilder()

    first = builder.build(package)
    second = builder.build(package)
    third = build_scene_prompt(
        ScenePackage.from_dict(package.to_dict())
    )
    restored = ScenePrompt.from_dict(first.to_dict())

    assert first == second == third == restored
    assert first.to_dict() == second.to_dict() == third.to_dict()
    assert first.to_json() == second.to_json() == third.to_json()
    assert restored.to_json() == first.to_json()


def test_scene_prompt_preserves_exact_narration_equations_code_labels_and_text(
    planning_input, script_payload, whiteboard_payload
):
    narration = "  Narration:\n    A neural network passes information forward.  "
    code = "\n  def forward(x):\n      return x\n"
    equation = "  y = 2x + 1  "
    label = "Label: Δx → 0"
    modified_script = copy.deepcopy(script_payload)
    modified_script["segments"][0]["text"] = narration
    modified_script["segments"][1]["text"] = code
    modified_script["segments"][2]["text"] = label
    modified_script["key_points"][0]["text"] = equation
    script = SceneScript.from_dict(modified_script)

    item = _visual_item(exact_text=(equation, code, label))
    modified_whiteboard = copy.deepcopy(whiteboard_payload)
    modified_whiteboard["drawing"]["actions"][0]["text"] = equation
    modified_whiteboard["drawing"]["actions"][0]["label"] = (
        "  Slope label: Δx  "
    )
    whiteboard = WhiteboardPlan.from_dict(modified_whiteboard)
    source_text = "  Source formula:\n    y = 2x + 1  "
    sources = (
        dataclasses.replace(
            planning_input.scene.source_excerpts[0],
            text=source_text,
        ),
        planning_input.scene.source_excerpts[1],
    )
    package = _package(
        planning_input,
        script_payload,
        whiteboard_payload,
        script=script,
        visual=_visual(items=(item,)),
        whiteboard=whiteboard,
        source_excerpts=sources,
    )

    prompt = build_scene_prompt(package)
    restored = ScenePrompt.from_dict(prompt.to_dict())
    serialized = json.loads(prompt.to_json())

    for candidate in (prompt, restored):
        assert candidate.script.segments[0].text == narration
        assert candidate.script.segments[1].text == code
        assert candidate.script.segments[2].text == label
        assert candidate.script.key_points[0].text == equation
        assert candidate.visual.items[0].exact_text == (
            equation,
            code,
            label,
        )
        assert candidate.whiteboard.drawing.actions[0].text == equation
        assert (
            candidate.whiteboard.drawing.actions[0].label
            == "  Slope label: Δx  "
        )
        assert candidate.source_excerpts[0].text == source_text

    assert serialized["script"]["segments"][0]["text"] == narration
    assert serialized["script"]["segments"][1]["text"] == code
    assert serialized["script"]["segments"][2]["text"] == label
    assert serialized["visual"]["items"][0]["exact_text"] == [
        equation,
        code,
        label,
    ]
    assert serialized["whiteboard"]["drawing"]["actions"][0]["text"] == (
        equation
    )
    assert serialized["source_excerpts"][0]["text"] == source_text


def test_scene_prompt_preserves_ordering_and_references(
    planning_input, script_payload, whiteboard_payload
):
    prompt = build_scene_prompt(
        _package(planning_input, script_payload, whiteboard_payload)
    )

    assert [
        segment.segment_id for segment in prompt.script.segments
    ] == ["seg-1", "seg-2", "seg-3"]
    assert [
        point.key_point_id for point in prompt.script.key_points
    ] == ["kp-1", "kp-2"]
    assert [
        item.visual_id for item in prompt.visual.items
    ] == ["vis-1", "vis-2"]
    assert prompt.visual.items[0].segment_ids == ("seg-1",)
    assert prompt.visual.items[0].key_point_ids == ("kp-1",)
    assert prompt.visual.items[0].source_ids == ("src-1",)
    assert prompt.visual.items[1].segment_ids == ("seg-2", "seg-3")
    assert prompt.visual.items[1].key_point_ids == ("kp-2",)
    assert prompt.visual.items[1].source_ids == ("src-2",)
    assert [
        action.action_id for action in prompt.whiteboard.drawing.actions
    ] == ["act-1", "act-2", "act-3", "act-4"]
    assert [
        excerpt.source_id for excerpt in prompt.source_excerpts
    ] == ["src-1", "src-2"]
    assert [
        entry.segment_id for entry in prompt.timing.timeline.segments
    ] == ["seg-1", "seg-2", "seg-3"]


def test_scene_prompt_keeps_visual_semantics_separate_from_whiteboard(
    planning_input, script_payload, whiteboard_payload
):
    prompt = build_scene_prompt(
        _package(planning_input, script_payload, whiteboard_payload)
    )
    visual_item = prompt.visual.items[0].to_dict()
    action = prompt.whiteboard.drawing.actions[0].to_dict()
    prompt_data = prompt.to_dict()

    assert prompt.visual.items[0].description == (
        "Show how layers pass information forward."
    )
    assert set(visual_item) == {
        "visual_id",
        "purpose",
        "kind",
        "description",
        "segment_ids",
        "key_point_ids",
        "source_ids",
        "exact_text",
        "accessibility_notes",
    }
    assert "region_id" not in visual_item
    assert "x" not in visual_item
    assert "order" not in visual_item
    assert action["region_id"] == "main"
    assert action["order"] == 1
    assert "visual" in prompt_data
    assert "whiteboard" in prompt_data
    assert prompt_data["visual"] is not prompt_data["whiteboard"]
    assert "layout" not in prompt_data["visual"]
    assert "drawing" not in prompt_data["visual"]
    assert "actions" not in prompt_data["visual"]


def test_scene_prompt_labels_duration_as_planning_estimate(
    planning_input, script_payload, whiteboard_payload
):
    package = _package(
        planning_input, script_payload, whiteboard_payload
    )
    prompt = build_scene_prompt(package)
    timing = prompt.timing
    timing_data = prompt.to_dict()["timing"]

    assert timing.basis is PromptTimingBasis.PLANNING_ESTIMATE
    assert timing_data["basis"] == "planning_estimate"
    assert timing.estimated_total_duration_seconds == pytest.approx(30.0)
    assert timing.target_duration_seconds == 30.0
    assert timing.timeline == package.timeline
    assert timing.notice == SCENE_PROMPT_TIMING_NOTICE
    assert "planning estimate" in timing.notice
    assert "not actual TTS" in timing.notice
    assert [
        entry.duration_seconds for entry in timing.timeline.segments
    ] == [12.0, 10.0, 8.0]
    assert "timing" in prompt.to_dict()
    assert "actual_audio" not in prompt.to_json()


@pytest.mark.parametrize("value", [None, {}, [], "scene_package"])
def test_scene_prompt_builder_rejects_non_package_input(value):
    builder = ScenePromptBuilder()

    with pytest.raises(
        ScenePackagingInputError,
        match="package must be a ScenePackage",
    ):
        builder.build(value)

    with pytest.raises(
        ScenePackagingInputError,
        match="package must be a ScenePackage",
    ):
        build_scene_prompt(value)


def test_scene_prompt_rejects_unknown_fields_and_bad_versions(
    planning_input, script_payload, whiteboard_payload
):
    prompt = build_scene_prompt(
        _package(planning_input, script_payload, whiteboard_payload)
    )

    unknown = prompt.to_dict()
    unknown["provider"] = "example"
    with pytest.raises(ContractError, match="unknown fields: provider"):
        ScenePrompt.from_dict(unknown)

    missing = prompt.to_dict()
    del missing["timing"]
    with pytest.raises(ContractError, match="missing required fields: timing"):
        ScenePrompt.from_dict(missing)

    bad_version = prompt.to_dict()
    bad_version["schema_version"] = "2.0"
    with pytest.raises(
        ContractError, match="scene_prompt.schema_version must be one of"
    ):
        ScenePrompt.from_dict(bad_version)

    bad_basis = prompt.to_dict()
    bad_basis["timing"]["basis"] = "actual_audio"
    with pytest.raises(
        ContractError, match="scene_prompt.timing.basis must be one of"
    ):
        ScenePrompt.from_dict(bad_basis)


def test_scene_prompt_rejects_inconsistent_canonical_content(
    planning_input, script_payload, whiteboard_payload
):
    package = _package(
        planning_input, script_payload, whiteboard_payload
    )
    bad_script_payload = copy.deepcopy(script_payload)
    bad_script_payload["scene_id"] = "scene-999"
    bad_script = SceneScript.from_dict(bad_script_payload)
    bad_timeline = _timeline(bad_script)

    with pytest.raises(
        ContractError,
        match="inconsistent with the canonical scene package",
    ):
        ScenePrompt(
            lesson_id=package.lesson_id,
            scene_id=package.scene_id,
            script=bad_script,
            visual=package.visual,
            whiteboard=package.whiteboard,
            source_excerpts=package.source_excerpts,
            timing=ScenePromptTiming(
                basis=PromptTimingBasis.PLANNING_ESTIMATE,
                timeline=bad_timeline,
            ),
        )


def test_scene_prompt_projects_package_without_invention_or_omission(
    planning_input, script_payload, whiteboard_payload
):
    package = _package(
        planning_input, script_payload, whiteboard_payload
    )
    prompt_data = build_scene_prompt(package).to_dict()
    package_data = package.to_dict()

    assert prompt_data["schema_version"] == SCENE_PROMPT_SCHEMA_VERSION
    assert set(prompt_data) == (
        set(package_data) - {"timeline"}
    ) | {"timing"}
    for field_name in (
        "lesson_id",
        "scene_id",
        "script",
        "visual",
        "whiteboard",
        "source_excerpts",
    ):
        assert prompt_data[field_name] == package_data[field_name]
    assert prompt_data["timing"]["timeline"] == package_data["timeline"]
    assert prompt_data["timing"]["basis"] == "planning_estimate"
    assert set(prompt_data["timing"]) == {"basis", "timeline"}


def test_scene_prompt_schema_versions_are_pinned() -> None:
    assert SCENE_PROMPT_SCHEMA_VERSION == "1.0"
    assert SUPPORTED_SCENE_PROMPT_SCHEMA_VERSIONS == frozenset({"1.0"})


def test_scene_prompt_builder_makes_no_external_calls(
    monkeypatch, planning_input, script_payload, whiteboard_payload
):
    def fail_network(*args, **kwargs):
        raise AssertionError("scene prompt building must not use the network")

    monkeypatch.setattr(socket, "socket", fail_network)
    monkeypatch.setattr(socket, "create_connection", fail_network)

    package = _package(
        planning_input, script_payload, whiteboard_payload
    )
    prompt = build_scene_prompt(package)

    assert isinstance(prompt, ScenePrompt)
    assert prompt.scene_id == "scene-001"
