"""Contract tests for the canonical provider-neutral scene package.

Responsibility: pin the composition and cross-references that make a
``ScenePackage`` safe to hand to packaging: scene identity, script/visual/
whiteboard/timeline consistency, source grounding, strict serialization, exact
text preservation, and planning-only timing semantics.
Workflow: deterministic unit tests with no provider, TTS, network, or rendering
calls.
Inputs: existing fixtures from ``tests/conftest.py`` and small local payloads.
Outputs: pytest assertions only.
Interacts with: ``video.scene_contract``, the scene/visual/source contracts,
``video.scene_timing``, and ``whiteboard.plan``.
"""

from __future__ import annotations

import copy
import dataclasses
import json

import pytest

from ai_video_generator.models.base import ContractError
from ai_video_generator.models.scene import SceneScript
from ai_video_generator.models.source import SourceExcerpt
from ai_video_generator.models.visual_plan import (
    VisualItem,
    VisualPlan,
)
from ai_video_generator.video.scene_contract import (
    SCENE_PACKAGE_SCHEMA_VERSION,
    SUPPORTED_SCENE_PACKAGE_SCHEMA_VERSIONS,
    ScenePackage,
)
from ai_video_generator.video.scene_timing import (
    DurationLimits,
    SceneTimeline,
    SegmentTimelineEntry,
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
        "description": "Show layers passing information forward.",
        "segment_ids": ["seg-1"],
        "key_point_ids": ["kp-1"],
        "source_ids": ["src-1"],
    }
    payload.update(overrides)
    return VisualItem.from_dict(payload)


def _visual(
    *, scene_id: str = "scene-001", items: tuple[VisualItem, ...] = ()
) -> VisualPlan:
    return VisualPlan(scene_id=scene_id, items=items)


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
    scene_id: str = "scene-001",
    lesson_id: str = "lesson-001",
) -> ScenePackage:
    resolved_script = (
        _script(script_payload) if script is None else script
    )
    resolved_whiteboard = (
        _whiteboard(whiteboard_payload)
        if whiteboard is None
        else whiteboard
    )
    resolved_visual = (
        _visual(items=(_visual_item(),))
        if visual is None
        else visual
    )
    resolved_timeline = (
        _timeline(resolved_script) if timeline is None else timeline
    )
    resolved_sources = (
        planning_input.scene.source_excerpts
        if source_excerpts is None
        else source_excerpts
    )
    return ScenePackage(
        lesson_id=lesson_id,
        scene_id=scene_id,
        script=resolved_script,
        visual=resolved_visual,
        whiteboard=resolved_whiteboard,
        timeline=resolved_timeline,
        source_excerpts=resolved_sources,
    )


def test_scene_package_accepts_all_required_components(
    planning_input, script_payload, whiteboard_payload
):
    script = _script(script_payload)
    whiteboard = _whiteboard(whiteboard_payload)
    visual = _visual(items=(_visual_item(),))
    timeline = _timeline(script)

    package = ScenePackage(
        lesson_id="lesson-001",
        scene_id="scene-001",
        script=script,
        visual=visual,
        whiteboard=whiteboard,
        timeline=timeline,
        source_excerpts=planning_input.scene.source_excerpts,
    )

    assert package.schema_version == SCENE_PACKAGE_SCHEMA_VERSION
    assert package.schema_version in SUPPORTED_SCENE_PACKAGE_SCHEMA_VERSIONS
    assert package.lesson_id == "lesson-001"
    assert package.scene_id == "scene-001"
    assert package.script is script
    assert package.visual is visual
    assert package.whiteboard is whiteboard
    assert package.timeline is timeline
    assert package.source_excerpts == planning_input.scene.source_excerpts
    assert package.visual.items[0].visual_id == "vis-1"


def test_scene_package_round_trips_through_strict_dict_parsing(
    planning_input, script_payload, whiteboard_payload
):
    package = _package(planning_input, script_payload, whiteboard_payload)

    restored = ScenePackage.from_dict(package.to_dict())

    assert restored == package
    assert restored.to_json() == package.to_json()
    assert isinstance(restored.script, SceneScript)
    assert isinstance(restored.visual, VisualPlan)
    assert isinstance(restored.whiteboard, WhiteboardPlan)
    assert isinstance(restored.timeline, SceneTimeline)
    assert all(
        isinstance(excerpt, SourceExcerpt)
        for excerpt in restored.source_excerpts
    )


def test_scene_package_serialization_is_deterministic(
    planning_input, script_payload, whiteboard_payload
):
    first = _package(planning_input, script_payload, whiteboard_payload)
    second = _package(planning_input, script_payload, whiteboard_payload)

    assert first == second
    assert first.to_json() == second.to_json()
    assert json.loads(first.to_json()) == first.to_dict()


def test_scene_package_preserves_exact_narration_and_visual_text(
    planning_input, script_payload, whiteboard_payload
):
    narration = "  Equation:\n    y = 2x + 1  "
    code = "\n  def forward(x):\n    return x\n"
    modified_script = copy.deepcopy(script_payload)
    modified_script["segments"][0]["text"] = narration
    modified_script["segments"][1]["text"] = code
    script = _script(modified_script)
    item = _visual_item(
        exact_text=[
            "  y = 2x + 1  ",
            "def forward(x):\n    return x",
        ]
    )
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
        source_excerpts=sources,
    )
    restored = ScenePackage.from_dict(package.to_dict())

    assert package.script.segments[0].text == narration
    assert package.script.segments[1].text == code
    assert package.visual.items[0].exact_text == (
        "  y = 2x + 1  ",
        "def forward(x):\n    return x",
    )
    assert package.source_excerpts[0].text == source_text
    assert restored.script.segments[0].text == narration
    assert restored.script.segments[1].text == code
    assert restored.visual.items[0].exact_text == (
        "  y = 2x + 1  ",
        "def forward(x):\n    return x",
    )
    assert restored.source_excerpts[0].text == source_text
    assert json.loads(restored.to_json())["script"]["segments"][0][
        "text"
    ] == narration


def test_scene_package_rejects_script_scene_mismatch(
    planning_input, script_payload, whiteboard_payload
):
    bad_script_payload = copy.deepcopy(script_payload)
    bad_script_payload["scene_id"] = "scene-999"
    bad_script = _script(bad_script_payload)

    with pytest.raises(ContractError, match="script.scene_id"):
        _package(
            planning_input,
            script_payload,
            whiteboard_payload,
            script=bad_script,
            timeline=_timeline(bad_script),
        )


def test_scene_package_rejects_visual_scene_mismatch(
    planning_input, script_payload, whiteboard_payload
):
    with pytest.raises(ContractError, match="visual.scene_id"):
        _package(
            planning_input,
            script_payload,
            whiteboard_payload,
            visual=_visual(scene_id="scene-999"),
        )


def test_scene_package_rejects_whiteboard_scene_mismatch(
    planning_input, script_payload, whiteboard_payload
):
    bad_whiteboard_payload = copy.deepcopy(whiteboard_payload)
    bad_whiteboard_payload["scene_id"] = "scene-999"

    with pytest.raises(ContractError, match="whiteboard.scene_id"):
        _package(
            planning_input,
            script_payload,
            whiteboard_payload,
            whiteboard=_whiteboard(bad_whiteboard_payload),
        )


def test_scene_package_rejects_timeline_scene_mismatch(
    planning_input, script_payload, whiteboard_payload
):
    bad_script_payload = copy.deepcopy(script_payload)
    bad_script_payload["scene_id"] = "scene-999"
    bad_timeline = _timeline(_script(bad_script_payload))

    with pytest.raises(ContractError, match="timeline.scene_id"):
        _package(
            planning_input,
            script_payload,
            whiteboard_payload,
            timeline=bad_timeline,
        )


def test_scene_package_rejects_timeline_segment_order_mismatch(
    planning_input, script_payload, whiteboard_payload
):
    reordered = SceneTimeline(
        scene_id="scene-001",
        segments=(
            SegmentTimelineEntry("seg-2", 0.0, 12.0, 12.0),
            SegmentTimelineEntry("seg-1", 12.0, 22.0, 10.0),
            SegmentTimelineEntry("seg-3", 22.0, 30.0, 8.0),
        ),
    )

    with pytest.raises(ContractError, match="segment_id order"):
        _package(
            planning_input,
            script_payload,
            whiteboard_payload,
            timeline=reordered,
        )


def test_scene_package_rejects_timeline_with_missing_segment(
    planning_input, script_payload, whiteboard_payload
):
    missing_segment = SceneTimeline(
        scene_id="scene-001",
        segments=(
            SegmentTimelineEntry("seg-1", 0.0, 12.0, 12.0),
            SegmentTimelineEntry("seg-3", 12.0, 20.0, 8.0),
        ),
    )

    with pytest.raises(ContractError, match="segment_id order"):
        _package(
            planning_input,
            script_payload,
            whiteboard_payload,
            timeline=missing_segment,
        )


def test_scene_package_rejects_visual_unknown_segment_id(
    planning_input, script_payload, whiteboard_payload
):
    visual = _visual(
        items=(_visual_item(segment_ids=["seg-999"]),)
    )

    with pytest.raises(
        ContractError, match="segment_ids references unknown narration"
    ):
        _package(
            planning_input,
            script_payload,
            whiteboard_payload,
            visual=visual,
        )


def test_scene_package_rejects_visual_unknown_key_point_id(
    planning_input, script_payload, whiteboard_payload
):
    visual = _visual(
        items=(_visual_item(key_point_ids=["kp-999"]),)
    )

    with pytest.raises(
        ContractError, match="key_point_ids references unknown"
    ):
        _package(
            planning_input,
            script_payload,
            whiteboard_payload,
            visual=visual,
        )


def test_scene_package_rejects_unknown_script_source_id(
    planning_input, script_payload, whiteboard_payload
):
    bad_script_payload = copy.deepcopy(script_payload)
    bad_script_payload["key_points"][0]["source_ids"] = ["src-999"]
    bad_script = _script(bad_script_payload)

    with pytest.raises(
        ContractError, match="key_points.*source_id\\(s\\).*src-999"
    ):
        _package(
            planning_input,
            script_payload,
            whiteboard_payload,
            script=bad_script,
            timeline=_timeline(bad_script),
        )


def test_scene_package_rejects_unknown_visual_source_id(
    planning_input, script_payload, whiteboard_payload
):
    visual = _visual(
        items=(_visual_item(source_ids=["src-999"]),)
    )

    with pytest.raises(
        ContractError, match="visual.items.*source_id\\(s\\).*src-999"
    ):
        _package(
            planning_input,
            script_payload,
            whiteboard_payload,
            visual=visual,
        )


def test_scene_package_accepts_valid_empty_visual_plan(
    planning_input, script_payload, whiteboard_payload
):
    package = _package(
        planning_input,
        script_payload,
        whiteboard_payload,
        visual=_visual(items=()),
    )

    assert package.visual.items == ()
    assert ScenePackage.from_dict(package.to_dict()) == package


def test_scene_package_rejects_whiteboard_unknown_segment_anchor(
    planning_input, script_payload, whiteboard_payload
):
    bad_whiteboard_payload = copy.deepcopy(whiteboard_payload)
    bad_whiteboard_payload["drawing"]["actions"][0]["segment_id"] = "seg-999"

    with pytest.raises(
        ContractError, match="whiteboard references unknown narration"
    ):
        _package(
            planning_input,
            script_payload,
            whiteboard_payload,
            whiteboard=_whiteboard(bad_whiteboard_payload),
        )


def test_scene_package_rejects_invalid_nested_contract_payloads(
    planning_input, script_payload, whiteboard_payload
):
    valid = _package(
        planning_input, script_payload, whiteboard_payload
    ).to_dict()

    missing_script_field = copy.deepcopy(valid)
    del missing_script_field["script"]["language"]
    with pytest.raises(ContractError, match="missing required fields: language"):
        ScenePackage.from_dict(missing_script_field)

    bad_visual_enum = copy.deepcopy(valid)
    bad_visual_enum["visual"]["items"][0]["purpose"] = "unknown_purpose"
    with pytest.raises(ContractError, match="visual_item.purpose must be one of"):
        ScenePackage.from_dict(bad_visual_enum)

    bad_whiteboard_geometry = copy.deepcopy(valid)
    bad_whiteboard_geometry["whiteboard"]["layout"]["regions"][0][
        "width"
    ] = 0.0
    with pytest.raises(ContractError, match="region.width must be at least"):
        ScenePackage.from_dict(bad_whiteboard_geometry)

    bad_timeline = copy.deepcopy(valid)
    bad_timeline["timeline"]["segments"][1]["start_seconds"] = 13.0
    bad_timeline["timeline"]["segments"][1]["duration_seconds"] = 9.0
    with pytest.raises(
        ContractError, match="start_seconds must equal the previous"
    ):
        ScenePackage.from_dict(bad_timeline)

    bad_source = copy.deepcopy(valid)
    del bad_source["source_excerpts"][0]["text"]
    with pytest.raises(ContractError, match="missing required fields: text"):
        ScenePackage.from_dict(bad_source)


def test_scene_package_rejects_missing_and_unexpected_fields(
    planning_input, script_payload, whiteboard_payload
):
    valid = _package(
        planning_input, script_payload, whiteboard_payload
    ).to_dict()

    missing = copy.deepcopy(valid)
    del missing["visual"]
    with pytest.raises(
        ContractError, match="missing required fields: visual"
    ):
        ScenePackage.from_dict(missing)

    unexpected = copy.deepcopy(valid)
    unexpected["provider_prompt"] = "Do not add this here."
    with pytest.raises(
        ContractError, match="unknown fields: provider_prompt"
    ):
        ScenePackage.from_dict(unexpected)

    nested_unexpected = copy.deepcopy(valid)
    nested_unexpected["script"]["segments"][0]["provider_field"] = "x"
    with pytest.raises(
        ContractError, match="segment contains unknown fields: provider_field"
    ):
        ScenePackage.from_dict(nested_unexpected)

    unknown_version = copy.deepcopy(valid)
    unknown_version["schema_version"] = "2.0"
    with pytest.raises(
        ContractError, match="schema_version must be one of"
    ):
        ScenePackage.from_dict(unknown_version)


def test_scene_package_is_immutable_and_preserves_collection_order(
    planning_input, script_payload, whiteboard_payload
):
    reversed_sources = tuple(
        reversed(planning_input.scene.source_excerpts)
    )
    items = (
        _visual_item(
            visual_id="vis-b",
            segment_ids=["seg-2"],
            key_point_ids=["kp-2"],
            source_ids=["src-2"],
        ),
        _visual_item(
            visual_id="vis-a",
            segment_ids=["seg-1"],
            key_point_ids=["kp-1"],
            source_ids=["src-1"],
        ),
    )
    package = _package(
        planning_input,
        script_payload,
        whiteboard_payload,
        visual=_visual(items=items),
        source_excerpts=reversed_sources,
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        package.lesson_id = "lesson-999"

    assert isinstance(package.script.segments, tuple)
    assert isinstance(package.visual.items, tuple)
    assert isinstance(package.whiteboard.drawing.actions, tuple)
    assert isinstance(package.timeline.segments, tuple)
    assert isinstance(package.source_excerpts, tuple)
    assert package.source_excerpts == reversed_sources
    assert [item.visual_id for item in package.visual.items] == [
        "vis-b",
        "vis-a",
    ]
    assert [
        excerpt.source_id for excerpt in package.source_excerpts
    ] == ["src-2", "src-1"]
    assert package.to_dict()["source_excerpts"][0]["source_id"] == "src-2"


def test_scene_package_timeline_remains_planning_estimates(
    planning_input, script_payload, whiteboard_payload
):
    package = _package(planning_input, script_payload, whiteboard_payload)

    assert [
        entry.duration_seconds for entry in package.timeline.segments
    ] == [
        segment.estimated_duration_seconds
        for segment in package.script.segments
    ]
    assert package.timeline.total_duration_seconds == pytest.approx(
        package.script.estimated_duration_seconds
    )
    assert not any(
        "actual" in field.name
        for field in dataclasses.fields(ScenePackage)
    )
    assert not any(
        key.startswith("actual_")
        for key in package.to_dict()["timeline"]
    )

    stretched = SceneTimeline(
        scene_id="scene-001",
        segments=(
            SegmentTimelineEntry("seg-1", 0.0, 10.0, 10.0),
            SegmentTimelineEntry("seg-2", 10.0, 20.0, 10.0),
            SegmentTimelineEntry("seg-3", 20.0, 28.0, 8.0),
        ),
    )

    with pytest.raises(
        ContractError, match="duration_seconds must match"
    ):
        _package(
            planning_input,
            script_payload,
            whiteboard_payload,
            timeline=stretched,
        )
