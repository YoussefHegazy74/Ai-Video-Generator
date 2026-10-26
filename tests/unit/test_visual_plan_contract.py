"""Contract tests for visual-plan and visual-planning models.

Responsibility: pin the provider-neutral visual contracts: required fields,
ordered items, stable IDs, enum handling, optional references, exact-text
preservation, strict deserialization, composition, and immutability.
Workflow: deterministic unit tests with no provider, LLM, network, or rendering
calls.
Inputs: small local payloads plus the existing scene fixtures from
``tests/conftest.py``.
Outputs: pytest assertions only.
Interacts with: ``models.visual_plan``, ``models.visual_planning``,
``models.scene``, and ``whiteboard.plan``.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import re

import pytest

from ai_video_generator.models.base import ContractError
from ai_video_generator.models.scene import SceneScript
from ai_video_generator.models.visual_plan import (
    MAX_VISUAL_ACCESSIBILITY_NOTES,
    MAX_VISUAL_DESCRIPTION_LENGTH,
    MAX_VISUAL_EXACT_TEXT_LENGTH,
    SUPPORTED_VISUAL_PLAN_SCHEMA_VERSIONS,
    VISUAL_PLAN_SCHEMA_VERSION,
    VisualItem,
    VisualKind,
    VisualPlan,
    VisualPurpose,
)
from ai_video_generator.models.visual_planning import (
    SUPPORTED_VISUAL_PLANNING_RESULT_SCHEMA_VERSIONS,
    VISUAL_PLANNING_RESULT_SCHEMA_VERSION,
    VisualPlanningInput,
    VisualPlanningResult,
)
from ai_video_generator.whiteboard.plan import WhiteboardPlan


def _item_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "visual_id": "vis-1",
        "purpose": "explain_concept",
        "kind": "diagram",
        "description": "Show how layers pass information forward.",
    }
    payload.update(overrides)
    return payload


def _item(**overrides: object) -> VisualItem:
    return VisualItem.from_dict(_item_payload(**overrides))


def _plan(*items: VisualItem, scene_id: str = "scene-001") -> VisualPlan:
    return VisualPlan(scene_id=scene_id, items=items)


def test_minimal_visual_plan_is_valid() -> None:
    item = _item()
    plan = _plan(item)

    assert plan.scene_id == "scene-001"
    assert plan.items == (item,)
    assert plan.schema_version == VISUAL_PLAN_SCHEMA_VERSION
    assert item.purpose is VisualPurpose.EXPLAIN_CONCEPT
    assert item.kind is VisualKind.DIAGRAM
    assert item.segment_ids == ()
    assert item.key_point_ids == ()
    assert item.source_ids == ()
    assert item.exact_text == ()
    assert item.accessibility_notes == ()


def test_multiple_items_keep_order_and_identity() -> None:
    items = (
        _item(visual_id="vis-intro", purpose="explain_concept"),
        _item(
            visual_id="vis-example",
            purpose="illustrate_example",
            kind="real_world_example",
        ),
        _item(
            visual_id="vis-summary",
            purpose="summarize_key_idea",
            kind="chart",
        ),
    )

    plan = _plan(*items)

    assert plan.items == items
    assert [item.visual_id for item in plan.items] == [
        "vis-intro",
        "vis-example",
        "vis-summary",
    ]


def test_duplicate_item_ids_are_rejected() -> None:
    with pytest.raises(ContractError, match="duplicate"):
        _plan(
            _item(visual_id="vis-1"),
            _item(visual_id="vis-1"),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("segment_ids", ["seg-1", "seg-1"]),
        ("key_point_ids", ["kp-1", "kp-1"]),
        ("source_ids", ["src-1", "src-1"]),
    ],
)
def test_duplicate_references_within_an_item_are_rejected(
    field: str, value: list[str]
) -> None:
    with pytest.raises(
        ContractError, match=f"visual_item.{field} contains duplicate"
    ):
        _item(**{field: value})


@pytest.mark.parametrize(
    "missing", ["visual_id", "purpose", "kind", "description"]
)
def test_required_item_fields_are_rejected_when_missing(
    missing: str,
) -> None:
    payload = _item_payload()
    del payload[missing]

    with pytest.raises(
        ContractError, match=f"missing required fields: {missing}"
    ):
        VisualItem.from_dict(payload)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"visual_id": ""}, "visual_item.visual_id must not be empty"),
        (
            {"visual_id": "has space"},
            "visual_item.visual_id must match",
        ),
        (
            {"purpose": "explain_everything"},
            "visual_item.purpose must be one of",
        ),
        (
            {"kind": "hologram"},
            "visual_item.kind must be one of",
        ),
        (
            {"description": "   "},
            "visual_item.description must not be empty",
        ),
        (
            {"description": "x" * (MAX_VISUAL_DESCRIPTION_LENGTH + 1)},
            "at most 2000 characters",
        ),
        (
            {"segment_ids": "seg-1"},
            "visual_item.segment_ids must be a list",
        ),
        (
            {"key_point_ids": [1]},
            "visual_item.key_point_ids[0] must be a string",
        ),
        (
            {"source_ids": ["bad id"]},
            "visual_item.source_ids[0] must match",
        ),
        (
            {"exact_text": ["   "]},
            "visual_item.exact_text[0] must not be empty",
        ),
        (
            {"exact_text": ["x" * (MAX_VISUAL_EXACT_TEXT_LENGTH + 1)]},
            "at most 2000 characters",
        ),
        (
            {"accessibility_notes": "note"},
            "visual_item.accessibility_notes must be a list",
        ),
    ],
)
def test_invalid_item_values_are_rejected(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ContractError, match=re.escape(message)):
        _item(**overrides)


def test_accessibility_notes_are_bounded() -> None:
    notes = [
        f"note {index}" for index in range(MAX_VISUAL_ACCESSIBILITY_NOTES + 1)
    ]

    with pytest.raises(ContractError, match="at most 20 item"):
        _item(accessibility_notes=notes)


def test_optional_fields_are_omitted_cleanly() -> None:
    plan = _plan(_item())
    data = plan.to_dict()

    assert data["items"][0]["purpose"] == "explain_concept"
    assert data["items"][0]["kind"] == "diagram"
    assert data["items"][0]["segment_ids"] == []
    assert data["items"][0]["key_point_ids"] == []
    assert data["items"][0]["source_ids"] == []
    assert data["items"][0]["exact_text"] == []
    assert data["items"][0]["accessibility_notes"] == []
    assert VisualPlan.from_dict(data) == plan


def test_optional_fields_and_exact_text_round_trip() -> None:
    formula = "  y = 2x + 1  "
    code = "\n  return layer(x)\n"
    item = _item(
        segment_ids=["seg-1", "seg-2"],
        key_point_ids=["kp-1"],
        source_ids=["src-1", "src-2"],
        exact_text=[formula, code],
        accessibility_notes=[
            "Use high contrast.",
            "Describe the arrows aloud.",
        ],
    )
    plan = _plan(item)

    restored = VisualPlan.from_dict(plan.to_dict())

    assert restored == plan
    assert restored.items[0].segment_ids == ("seg-1", "seg-2")
    assert restored.items[0].key_point_ids == ("kp-1",)
    assert restored.items[0].source_ids == ("src-1", "src-2")
    assert restored.items[0].exact_text == (formula, code)
    assert restored.items[0].accessibility_notes == (
        "Use high contrast.",
        "Describe the arrows aloud.",
    )
    assert restored.to_json() == plan.to_json()


def test_source_references_are_plain_stable_ids() -> None:
    item = _item(source_ids=["src-2", "src-1"])

    assert item.source_ids == ("src-2", "src-1")
    assert item.to_dict()["source_ids"] == ["src-2", "src-1"]
    assert VisualItem.from_dict(item.to_dict()) == item


def test_enum_values_are_stable() -> None:
    assert {purpose.value for purpose in VisualPurpose} == {
        "explain_concept",
        "illustrate_example",
        "show_process",
        "compare_alternatives",
        "summarize_key_idea",
    }
    assert {kind.value for kind in VisualKind} == {
        "diagram",
        "real_world_example",
        "analogy",
        "illustration",
        "chart",
        "other",
    }


def test_string_enum_values_are_coerced() -> None:
    item = VisualItem(
        visual_id="vis-1",
        purpose="show_process",
        kind="chart",
        description="Trace the data path.",
    )

    assert item.purpose is VisualPurpose.SHOW_PROCESS
    assert item.kind is VisualKind.CHART


def test_empty_plan_is_valid_and_round_trips() -> None:
    plan = VisualPlan(scene_id="scene-001", items=())

    restored = VisualPlan.from_dict(
        {"scene_id": "scene-001", "items": []}
    )

    assert plan.items == ()
    assert restored == plan
    assert restored.to_dict()["items"] == []
    assert restored.to_json() == plan.to_json()


def test_plan_identity_and_schema_are_validated() -> None:
    with pytest.raises(ContractError, match="visual_plan.scene_id"):
        VisualPlan(scene_id="", items=(_item(),))

    with pytest.raises(
        ContractError, match="schema_version must be one of"
    ):
        VisualPlan(
            scene_id="scene-001",
            items=(_item(),),
            schema_version="2.0",
        )


def test_plan_rejects_unknown_fields() -> None:
    payload = {
        "scene_id": "scene-001",
        "items": [_item_payload()],
        "extra": True,
    }

    with pytest.raises(ContractError, match="unknown fields: extra"):
        VisualPlan.from_dict(payload)


def test_plan_rejects_non_list_items() -> None:
    with pytest.raises(
        ContractError, match="visual_plan.items must be a list"
    ):
        VisualPlan.from_dict({"scene_id": "scene-001", "items": "nope"})


def test_plan_rejects_non_object_items() -> None:
    with pytest.raises(
        ContractError, match="visual_item must be an object"
    ):
        VisualPlan.from_dict(
            {"scene_id": "scene-001", "items": ["nope"]}
        )


def test_plan_requires_visual_item_objects() -> None:
    with pytest.raises(
        ContractError, match="must contain VisualItem objects"
    ):
        VisualPlan(
            scene_id="scene-001",
            items=({"visual_id": "vis-1"},),  # type: ignore[arg-type]
        )


def test_item_rejects_unknown_fields() -> None:
    with pytest.raises(
        ContractError, match="visual_item contains unknown fields: extra"
    ):
        VisualItem.from_dict(_item_payload(extra="nope"))


def test_models_are_immutable() -> None:
    item = _item()
    plan = _plan(item)

    with pytest.raises(dataclasses.FrozenInstanceError):
        item.description = "changed"

    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.items = ()


def test_serialization_is_deterministic() -> None:
    first = _plan(_item(segment_ids=["seg-2", "seg-1"]))
    second = _plan(_item(segment_ids=["seg-2", "seg-1"]))

    assert first == second
    assert first.to_json() == second.to_json()
    assert first.to_json() == json.dumps(
        first.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def test_visual_item_has_no_rendering_or_asset_fields() -> None:
    assert set(VisualItem.__dataclass_fields__) == {
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


def test_schema_versions_are_pinned() -> None:
    assert VISUAL_PLAN_SCHEMA_VERSION == "1.0"
    assert SUPPORTED_VISUAL_PLAN_SCHEMA_VERSIONS == frozenset({"1.0"})
    assert VISUAL_PLANNING_RESULT_SCHEMA_VERSION == "1.0"
    assert SUPPORTED_VISUAL_PLANNING_RESULT_SCHEMA_VERSIONS == frozenset(
        {"1.0"}
    )


def test_visual_planning_input_composes_existing_contracts(
    planning_input, script_payload
) -> None:
    script = SceneScript.from_dict(script_payload)

    visual_input = VisualPlanningInput(
        planning_input=planning_input,
        script=script,
    )

    assert visual_input.planning_input is planning_input
    assert visual_input.script is script
    assert visual_input.whiteboard is None
    assert (
        visual_input.planning_input.scene.scene_id == script.scene_id
    )
    assert (
        VisualPlanningInput.from_dict(visual_input.to_dict())
        == visual_input
    )


def test_visual_planning_input_accepts_matching_whiteboard(
    planning_input, script_payload, whiteboard_payload
) -> None:
    script = SceneScript.from_dict(script_payload)
    whiteboard = WhiteboardPlan.from_dict(whiteboard_payload)

    visual_input = VisualPlanningInput(
        planning_input=planning_input,
        script=script,
        whiteboard=whiteboard,
    )

    assert visual_input.whiteboard == whiteboard
    assert (
        VisualPlanningInput.from_dict(visual_input.to_dict())
        == visual_input
    )


def test_visual_planning_input_rejects_script_scene_mismatch(
    planning_input, script_payload
) -> None:
    script = SceneScript.from_dict(script_payload)
    mismatched = dataclasses.replace(script, scene_id="scene-999")

    with pytest.raises(
        ContractError,
        match="visual_input.script.scene_id must match",
    ):
        VisualPlanningInput(
            planning_input=planning_input,
            script=mismatched,
        )


def test_visual_planning_input_rejects_whiteboard_scene_mismatch(
    planning_input, script_payload, whiteboard_payload
) -> None:
    script = SceneScript.from_dict(script_payload)
    whiteboard = WhiteboardPlan.from_dict(whiteboard_payload)
    mismatched = dataclasses.replace(whiteboard, scene_id="scene-999")

    with pytest.raises(
        ContractError,
        match="visual_input.whiteboard.scene_id must match",
    ):
        VisualPlanningInput(
            planning_input=planning_input,
            script=script,
            whiteboard=mismatched,
        )


def test_visual_planning_input_rejects_unknown_whiteboard_segments(
    planning_input, script_payload, whiteboard_payload
) -> None:
    script = SceneScript.from_dict(script_payload)
    payload = copy.deepcopy(whiteboard_payload)
    for action in payload["drawing"]["actions"]:
        if action["segment_id"] == "seg-1":
            action["segment_id"] = "seg-missing"
        if action["segment_id"] == "seg-2":
            action["segment_id"] = "seg-missing"
        if action["segment_id"] == "seg-3":
            action["segment_id"] = "seg-missing"
    whiteboard = WhiteboardPlan.from_dict(payload)

    with pytest.raises(
        ContractError, match="unknown narration segment_id"
    ):
        VisualPlanningInput(
            planning_input=planning_input,
            script=script,
            whiteboard=whiteboard,
        )


def test_visual_planning_input_rejects_wrong_types(
    planning_input, script_payload
) -> None:
    script = SceneScript.from_dict(script_payload)

    with pytest.raises(
        ContractError,
        match="planning_input must be a ScenePlanningInput",
    ):
        VisualPlanningInput(
            planning_input=object(),  # type: ignore[arg-type]
            script=script,
        )

    with pytest.raises(
        ContractError, match="script must be a SceneScript"
    ):
        VisualPlanningInput(
            planning_input=planning_input,
            script=object(),  # type: ignore[arg-type]
        )

    with pytest.raises(
        ContractError,
        match="whiteboard must be a WhiteboardPlan or None",
    ):
        VisualPlanningInput(
            planning_input=planning_input,
            script=script,
            whiteboard=object(),  # type: ignore[arg-type]
        )


def test_visual_planning_input_rejects_unknown_fields(
    planning_input, script_payload
) -> None:
    payload = {
        "planning_input": planning_input.to_dict(),
        "script": script_payload,
        "extra": True,
    }

    with pytest.raises(
        ContractError, match="unknown fields: extra"
    ):
        VisualPlanningInput.from_dict(payload)


def test_visual_planning_result_round_trip() -> None:
    result = VisualPlanningResult(
        scene_id="scene-001",
        plan=_plan(_item()),
    )

    restored = VisualPlanningResult.from_dict(result.to_dict())

    assert result.schema_version == VISUAL_PLANNING_RESULT_SCHEMA_VERSION
    assert restored == result
    assert restored.to_json() == result.to_json()


def test_visual_planning_result_accepts_an_empty_plan() -> None:
    result = VisualPlanningResult(
        scene_id="scene-001",
        plan=VisualPlan(scene_id="scene-001", items=()),
    )

    assert result.plan.items == ()
    assert VisualPlanningResult.from_dict(result.to_dict()) == result


def test_visual_planning_result_rejects_scene_mismatch() -> None:
    with pytest.raises(
        ContractError, match="scene_id must match"
    ):
        VisualPlanningResult(
            scene_id="scene-999",
            plan=_plan(_item()),
        )


def test_visual_planning_result_validates_plan_type_and_schema() -> None:
    plan = _plan(_item())

    with pytest.raises(
        ContractError, match="plan must be a VisualPlan"
    ):
        VisualPlanningResult(
            scene_id="scene-001",
            plan=object(),  # type: ignore[arg-type]
        )

    with pytest.raises(
        ContractError, match="schema_version must be one of"
    ):
        VisualPlanningResult(
            scene_id="scene-001",
            plan=plan,
            schema_version="2.0",
        )

    with pytest.raises(
        ContractError, match="schema_version must be one of"
    ):
        VisualPlanningResult.from_dict(
            {
                "scene_id": "scene-001",
                "plan": plan.to_dict(),
                "schema_version": "2.0",
            }
        )
