"""Unit tests for the deterministic Scene Packager.

Responsibility: verify canonical package assembly, scene identity, exact text
preservation, target-duration propagation, duration blocking/warning policy,
reference validation, typed input failures, determinism, and absence of
external calls.
Workflow: isolated unit tests using the existing deterministic fixtures; no
LLM, provider, TTS, network, rendering, or paid API access.
Inputs: ``ScenePlanningResult``, ``VisualPlanningResult``, and
``DurationLimits`` values built from ``tests/conftest.py`` fixtures.
Outputs: pytest assertions only.
Interacts with: ``agents.scene_packaging``, ``video.scene_contract``,
``video.scene_timing``, and the scene/visual/whiteboard contracts.
"""

from __future__ import annotations

import copy
import dataclasses
import socket

import pytest

from ai_video_generator.agents.scene_packaging import (
    SceneDurationError,
    ScenePackager,
    ScenePackagingInputError,
)
from ai_video_generator.models.base import ContractError
from ai_video_generator.models.scene import SceneScript
from ai_video_generator.models.scene_planning import ScenePlanningResult
from ai_video_generator.models.visual_plan import VisualItem, VisualPlan
from ai_video_generator.models.visual_planning import VisualPlanningResult
from ai_video_generator.video.scene_contract import ScenePackage
from ai_video_generator.video.scene_timing import (
    DurationLimits,
    TimingSeverity,
)
from ai_video_generator.whiteboard.plan import WhiteboardPlan


def _planning_result(
    planning_input,
    script_payload: dict[str, object],
    whiteboard_payload: dict[str, object],
    *,
    scene=None,
    script: SceneScript | None = None,
    whiteboard: WhiteboardPlan | None = None,
) -> ScenePlanningResult:
    return ScenePlanningResult(
        lesson_id=planning_input.lesson_id,
        scene=planning_input.scene if scene is None else scene,
        script=(
            SceneScript.from_dict(copy.deepcopy(script_payload))
            if script is None
            else script
        ),
        whiteboard=(
            WhiteboardPlan.from_dict(copy.deepcopy(whiteboard_payload))
            if whiteboard is None
            else whiteboard
        ),
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


def _visual_result(
    *,
    scene_id: str = "scene-001",
    items: tuple[VisualItem, ...] = (),
) -> VisualPlanningResult:
    return VisualPlanningResult(
        scene_id=scene_id,
        plan=VisualPlan(scene_id=scene_id, items=items),
    )


def _limits(
    *,
    minimum_seconds: float = 1.0,
    maximum_seconds: float = 60.0,
    tolerance_seconds: float = 0.0,
) -> DurationLimits:
    return DurationLimits(
        minimum_seconds=minimum_seconds,
        maximum_seconds=maximum_seconds,
        tolerance_seconds=tolerance_seconds,
    )


def test_packager_assembles_canonical_package(
    planning_input, script_payload, whiteboard_payload
):
    planning = _planning_result(
        planning_input, script_payload, whiteboard_payload
    )
    visual = _visual_result(items=(_visual_item(),))

    package = ScenePackager().package(planning, visual, _limits())

    assert isinstance(package, ScenePackage)
    assert package.lesson_id == "lesson-001"
    assert package.scene_id == "scene-001"
    assert package.script is planning.script
    assert package.visual is visual.plan
    assert package.whiteboard is planning.whiteboard
    assert package.source_excerpts == planning.scene.source_excerpts
    assert package.timeline.scene_id == "scene-001"
    assert [entry.segment_id for entry in package.timeline.segments] == [
        "seg-1",
        "seg-2",
        "seg-3",
    ]
    assert [
        entry.start_seconds for entry in package.timeline.segments
    ] == [0.0, 12.0, 22.0]
    assert [
        entry.end_seconds for entry in package.timeline.segments
    ] == [12.0, 22.0, 30.0]
    assert [
        entry.duration_seconds for entry in package.timeline.segments
    ] == [
        segment.estimated_duration_seconds
        for segment in package.script.segments
    ]
    assert package.timeline.total_duration_seconds == pytest.approx(30.0)
    assert package.timeline.findings == ()
    assert ScenePackage.from_dict(package.to_dict()) == package


def test_packager_propagates_scene_brief_target_duration(
    planning_input, script_payload, whiteboard_payload
):
    scene = dataclasses.replace(
        planning_input.scene, target_duration_seconds=42.0
    )
    planning = _planning_result(
        planning_input, script_payload, whiteboard_payload, scene=scene
    )

    package = ScenePackager().package(
        planning,
        _visual_result(),
        _limits(tolerance_seconds=20.0),
    )

    assert package.timeline.target_duration_seconds == 42.0
    assert package.timeline.total_duration_seconds == pytest.approx(30.0)
    assert package.timeline.findings == ()
    assert [
        entry.duration_seconds for entry in package.timeline.segments
    ] == [12.0, 10.0, 8.0]


@pytest.mark.parametrize(
    ("limits", "target_duration_seconds", "expected_code"),
    [
        (
            _limits(maximum_seconds=20.0),
            20.0,
            "timing.total_above_maximum",
        ),
        (
            _limits(minimum_seconds=31.0),
            40.0,
            "timing.total_below_minimum",
        ),
    ],
)
def test_packager_blocks_duration_bound_errors(
    planning_input,
    script_payload,
    whiteboard_payload,
    limits,
    target_duration_seconds,
    expected_code,
):
    scene = dataclasses.replace(
        planning_input.scene,
        target_duration_seconds=target_duration_seconds,
    )
    planning = _planning_result(
        planning_input, script_payload, whiteboard_payload, scene=scene
    )

    with pytest.raises(SceneDurationError) as excinfo:
        ScenePackager().package(planning, _visual_result(), limits)

    assert [finding.code for finding in excinfo.value.findings] == [
        expected_code
    ]
    assert expected_code in str(excinfo.value)
    assert excinfo.value.details


def test_packager_blocks_target_duration_outside_limits(
    planning_input, script_payload, whiteboard_payload
):
    scene = dataclasses.replace(
        planning_input.scene, target_duration_seconds=45.0
    )
    planning = _planning_result(
        planning_input, script_payload, whiteboard_payload, scene=scene
    )

    with pytest.raises(SceneDurationError) as excinfo:
        ScenePackager().package(
            planning,
            _visual_result(),
            _limits(maximum_seconds=40.0),
        )

    assert [finding.code for finding in excinfo.value.findings] == [
        "timing.target_above_maximum"
    ]


def test_packager_allows_and_preserves_target_mismatch_warning(
    planning_input, script_payload, whiteboard_payload
):
    scene = dataclasses.replace(
        planning_input.scene, target_duration_seconds=42.0
    )
    planning = _planning_result(
        planning_input, script_payload, whiteboard_payload, scene=scene
    )

    package = ScenePackager().package(
        planning, _visual_result(), _limits()
    )

    warnings = [
        finding
        for finding in package.timeline.findings
        if finding.severity is TimingSeverity.WARNING
    ]
    assert [finding.code for finding in warnings] == [
        "timing.target_mismatch"
    ]
    assert warnings[0].path == "timeline.target_duration_seconds"
    assert not any(
        finding.severity is TimingSeverity.ERROR
        for finding in package.timeline.findings
    )
    assert package.timeline.target_duration_seconds == 42.0
    assert package.timeline.total_duration_seconds == pytest.approx(30.0)

    restored = ScenePackage.from_dict(package.to_dict())
    assert restored.timeline.findings == package.timeline.findings


def test_packager_preserves_exact_narration_formula_code_and_labels(
    planning_input, script_payload, whiteboard_payload
):
    exact_texts = (
        "  Formula:\n    y = 2x + 1  ",
        "\n  def forward(x):\n      return x\n",
        "Label: Δx → 0",
    )
    modified_payload = copy.deepcopy(script_payload)
    for segment, text in zip(modified_payload["segments"], exact_texts):
        segment["text"] = text
    script = SceneScript.from_dict(modified_payload)
    planning = _planning_result(
        planning_input, script_payload, whiteboard_payload, script=script
    )
    item = _visual_item(
        exact_text=(
            "  y = 2x + 1  ",
            "def forward(x):\n      return x",
        )
    )

    package = ScenePackager().package(
        planning, _visual_result(items=(item,)), _limits()
    )
    restored = ScenePackage.from_dict(package.to_dict())

    assert tuple(
        segment.text for segment in package.script.segments
    ) == exact_texts
    assert tuple(
        segment.text for segment in restored.script.segments
    ) == exact_texts
    assert package.visual.items[0].exact_text == (
        "  y = 2x + 1  ",
        "def forward(x):\n      return x",
    )
    assert restored.visual.items[0].exact_text == (
        "  y = 2x + 1  ",
        "def forward(x):\n      return x",
    )
    assert (
        package.whiteboard.drawing.actions[0].text
        == "Neural network = connected layers"
    )
    assert [
        entry.segment_id for entry in package.timeline.segments
    ] == [segment.segment_id for segment in script.segments]


def test_packager_accepts_valid_empty_visual_plan(
    planning_input, script_payload, whiteboard_payload
):
    planning = _planning_result(
        planning_input, script_payload, whiteboard_payload
    )

    package = ScenePackager().package(
        planning, _visual_result(items=()), _limits()
    )

    assert package.visual.items == ()
    assert ScenePackage.from_dict(package.to_dict()).visual.items == ()


def test_packager_rejects_visual_scene_id_mismatch(
    planning_input, script_payload, whiteboard_payload
):
    planning = _planning_result(
        planning_input, script_payload, whiteboard_payload
    )
    visual = _visual_result(scene_id="scene-999")

    with pytest.raises(
        ScenePackagingInputError, match="visual_result.scene_id"
    ):
        ScenePackager().package(planning, visual, _limits())

    mismatched_plan = _visual_result()
    object.__setattr__(
        mismatched_plan,
        "plan",
        VisualPlan(scene_id="scene-999", items=()),
    )
    with pytest.raises(
        ScenePackagingInputError, match="visual_result.plan.scene_id"
    ):
        ScenePackager().package(planning, mismatched_plan, _limits())


@pytest.mark.parametrize("component", ["script", "whiteboard"])
def test_packager_rejects_planning_component_scene_id_mismatch(
    planning_input,
    script_payload,
    whiteboard_payload,
    component,
):
    planning = _planning_result(
        planning_input, script_payload, whiteboard_payload
    )
    if component == "script":
        modified = copy.deepcopy(script_payload)
        modified["scene_id"] = "scene-999"
        replacement = SceneScript.from_dict(modified)
    else:
        modified = copy.deepcopy(whiteboard_payload)
        modified["scene_id"] = "scene-999"
        replacement = WhiteboardPlan.from_dict(modified)
    object.__setattr__(planning, component, replacement)

    with pytest.raises(ScenePackagingInputError, match=f"{component}.scene_id"):
        ScenePackager().package(planning, _visual_result(), _limits())


@pytest.mark.parametrize(
    ("field_name", "unknown_id", "message"),
    [
        (
            "segment_ids",
            "seg-999",
            "segment_ids references unknown narration",
        ),
        (
            "key_point_ids",
            "kp-999",
            "key_point_ids references unknown",
        ),
        (
            "source_ids",
            "src-999",
            "source_ids references source_id",
        ),
    ],
)
def test_packager_rejects_unknown_visual_references(
    planning_input,
    script_payload,
    whiteboard_payload,
    field_name,
    unknown_id,
    message,
):
    planning = _planning_result(
        planning_input, script_payload, whiteboard_payload
    )
    item = _visual_item(**{field_name: [unknown_id]})

    with pytest.raises(ContractError, match=message):
        ScenePackager().package(
            planning, _visual_result(items=(item,)), _limits()
        )


def test_packager_rejects_unknown_script_source_reference(
    planning_input, script_payload, whiteboard_payload
):
    planning = _planning_result(
        planning_input, script_payload, whiteboard_payload
    )
    modified = copy.deepcopy(script_payload)
    modified["key_points"][0]["source_ids"] = ["src-999"]
    object.__setattr__(
        planning, "script", SceneScript.from_dict(modified)
    )

    with pytest.raises(ContractError, match="key_points.*source_id"):
        ScenePackager().package(planning, _visual_result(), _limits())


def test_packager_rejects_unknown_whiteboard_segment_anchor(
    planning_input, script_payload, whiteboard_payload
):
    planning = _planning_result(
        planning_input, script_payload, whiteboard_payload
    )
    modified = copy.deepcopy(whiteboard_payload)
    modified["drawing"]["actions"][0]["segment_id"] = "seg-999"
    object.__setattr__(
        planning, "whiteboard", WhiteboardPlan.from_dict(modified)
    )

    with pytest.raises(
        ContractError, match="whiteboard references unknown narration"
    ):
        ScenePackager().package(planning, _visual_result(), _limits())


def test_packager_rejects_missing_required_inputs(
    planning_input, script_payload, whiteboard_payload
):
    planning = _planning_result(
        planning_input, script_payload, whiteboard_payload
    )
    visual = _visual_result()
    limits = _limits()
    packager = ScenePackager()

    with pytest.raises(
        ScenePackagingInputError,
        match="planning_result must be a ScenePlanningResult",
    ):
        packager.package(None, visual, limits)
    with pytest.raises(
        ScenePackagingInputError,
        match="visual_result must be a VisualPlanningResult",
    ):
        packager.package(planning, None, limits)
    with pytest.raises(
        ScenePackagingInputError,
        match="limits must be a DurationLimits",
    ):
        packager.package(planning, visual, None)


def test_packager_rejects_missing_source_context(
    planning_input, script_payload, whiteboard_payload
):
    planning = _planning_result(
        planning_input, script_payload, whiteboard_payload
    )
    object.__setattr__(planning.scene, "source_excerpts", ())

    with pytest.raises(
        ScenePackagingInputError,
        match="source_excerpts must contain at least one",
    ):
        ScenePackager().package(planning, _visual_result(), _limits())


def test_packager_rejects_invalid_input_types(
    planning_input, script_payload, whiteboard_payload
):
    planning = _planning_result(
        planning_input, script_payload, whiteboard_payload
    )
    visual = _visual_result()
    limits = _limits()
    packager = ScenePackager()

    with pytest.raises(
        ScenePackagingInputError,
        match="planning_result must be a ScenePlanningResult",
    ):
        packager.package({}, visual, limits)
    with pytest.raises(
        ScenePackagingInputError,
        match="visual_result must be a VisualPlanningResult",
    ):
        packager.package(planning, {"scene_id": "scene-001"}, limits)
    with pytest.raises(
        ScenePackagingInputError,
        match="limits must be a DurationLimits",
    ):
        packager.package(planning, visual, {"minimum_seconds": 1.0})


def test_packager_is_deterministic_across_repeated_calls(
    planning_input, script_payload, whiteboard_payload
):
    planning = _planning_result(
        planning_input, script_payload, whiteboard_payload
    )
    visual = _visual_result(items=(_visual_item(),))
    packager = ScenePackager()
    limits = _limits()

    first = packager.package(planning, visual, limits)
    second = packager.package(planning, visual, limits)

    assert first == second
    assert first.to_json() == second.to_json()
    assert first.to_dict() == second.to_dict()


def test_packager_makes_no_external_calls(
    monkeypatch, planning_input, script_payload, whiteboard_payload
):
    def fail_network(*args, **kwargs):
        raise AssertionError("scene packaging must not use the network")

    monkeypatch.setattr(socket, "socket", fail_network)
    monkeypatch.setattr(socket, "create_connection", fail_network)

    planning = _planning_result(
        planning_input, script_payload, whiteboard_payload
    )

    package = ScenePackager().package(
        planning, _visual_result(items=(_visual_item(),)), _limits()
    )

    assert isinstance(package, ScenePackage)
    assert package.scene_id == "scene-001"
