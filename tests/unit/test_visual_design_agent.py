"""Unit tests for the Example & Visual Agent.

Responsibility: verify structured prompt construction, visual-plan parsing,
scene identity, reference validation, exact-text preservation, empty plans,
learner-preference handling, and typed failure behavior.
Workflow: deterministic unit tests with a fake LLM; no network or credentials.
Inputs: fixtures from ``tests/conftest.py`` and small local payloads.
Outputs: pytest assertions only.
Interacts with: ``VisualDesignAgent``, visual-plan prompts, schemas, validation,
and the existing scene contracts.
"""

from __future__ import annotations

import copy
import dataclasses
import json

import pytest

from ai_video_generator.agents.scene_planning.errors import (
    AgentInputError,
    AgentOutputError,
    LLMProviderError,
)
from ai_video_generator.agents.visual_design import VisualDesignAgent
from ai_video_generator.agents.visual_design.prompts import (
    VISUAL_PROMPT_VERSION,
    build_visual_prompt,
)
from ai_video_generator.agents.visual_design.schemas import (
    VISUAL_PLAN_OUTPUT_SCHEMA,
)
from ai_video_generator.agents.visual_design.validation import (
    validate_visual_input,
    validate_visual_plan,
    validate_visual_result,
)
from ai_video_generator.models.lesson_plan import (
    AudienceLevel,
    PersonalizationBrief,
)
from ai_video_generator.models.scene import SceneScript
from ai_video_generator.models.visual_plan import (
    MAX_VISUAL_ACCESSIBILITY_NOTES,
    MAX_VISUAL_DESCRIPTION_LENGTH,
    MAX_VISUAL_EXACT_TEXT_LENGTH,
    VISUAL_PLAN_SCHEMA_VERSION,
    VisualKind,
    VisualPlan,
    VisualPurpose,
)
from ai_video_generator.models.visual_planning import (
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
        "segment_ids": ["seg-1"],
        "key_point_ids": ["kp-1"],
        "source_ids": ["src-1"],
    }
    payload.update(overrides)
    return payload


def _plan_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "scene_id": "scene-001",
        "items": [_item_payload()],
    }
    payload.update(overrides)
    return payload


def _script(script_payload: dict[str, object]) -> SceneScript:
    return SceneScript.from_dict(script_payload)


def _visual_input(
    planning_input,
    script: SceneScript,
    whiteboard: WhiteboardPlan | None = None,
) -> VisualPlanningInput:
    return VisualPlanningInput(
        planning_input=planning_input,
        script=script,
        whiteboard=whiteboard,
    )


def _agent(fake_llm_factory, payload) -> VisualDesignAgent:
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return VisualDesignAgent(fake_llm_factory(content))


class _RawResponseClient:
    """Client that returns a deliberately unsupported response object."""

    def __init__(self, response: object) -> None:
        self.response = response
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return self.response


def test_visual_agent_generates_validated_result(
    planning_input, script_payload, fake_llm_factory
):
    script = _script(script_payload)
    payload = _plan_payload(
        items=[
            _item_payload(
                visual_id="vis-layers",
                purpose="explain_concept",
                kind="diagram",
                description=(
                    "Show each layer as a group of players passing the ball "
                    "forward to the next group."
                ),
                segment_ids=["seg-1"],
                key_point_ids=["kp-1"],
                source_ids=["src-1"],
                exact_text=["Layer 1 → Layer 2"],
                accessibility_notes=["Describe the flow direction aloud."],
            ),
            _item_payload(
                visual_id="vis-training",
                purpose="illustrate_example",
                kind="analogy",
                description=(
                    "Compare training to a practice drill that adjusts passes "
                    "after each attempt."
                ),
                segment_ids=["seg-2", "seg-3"],
                key_point_ids=["kp-2"],
                source_ids=["src-2"],
            ),
        ]
    )
    llm = fake_llm_factory(json.dumps(payload))
    agent = VisualDesignAgent(llm)

    result = agent.generate(_visual_input(planning_input, script))

    assert isinstance(result, VisualPlanningResult)
    assert result.scene_id == "scene-001"
    assert result.plan.scene_id == "scene-001"
    assert [item.visual_id for item in result.plan.items] == [
        "vis-layers",
        "vis-training",
    ]
    assert result.plan.items[0].purpose is VisualPurpose.EXPLAIN_CONCEPT
    assert result.plan.items[1].kind is VisualKind.ANALOGY
    assert result.plan.items[0].segment_ids == ("seg-1",)
    assert result.plan.items[0].key_point_ids == ("kp-1",)
    assert result.plan.items[0].source_ids == ("src-1",)
    assert result.plan.items[1].segment_ids == ("seg-2", "seg-3")
    assert result.plan.items[1].key_point_ids == ("kp-2",)
    assert result.plan.items[1].source_ids == ("src-2",)
    assert result.plan.items[0].exact_text == ("Layer 1 → Layer 2",)
    assert result.plan.items[0].accessibility_notes == (
        "Describe the flow direction aloud.",
    )

    assert len(llm.requests) == 1
    request = llm.requests[0]
    assert request.response_schema_name == "VisualPlan"
    assert request.response_schema["title"] == "VisualPlan"
    assert request.metadata == {
        "agent": "visual_design_agent",
        "prompt_version": "1.0.0",
        "lesson_id": "lesson-001",
        "scene_id": "scene-001",
    }
    assert request.temperature == 0.2


def test_visual_agent_generate_for_scene_convenience(
    planning_input, script_payload, whiteboard_payload, fake_llm_factory
):
    script = _script(script_payload)
    whiteboard = WhiteboardPlan.from_dict(whiteboard_payload)
    llm = fake_llm_factory(json.dumps(_plan_payload()))
    agent = VisualDesignAgent(llm)

    result = agent.generate_for_scene(
        planning_input,
        script,
        whiteboard=whiteboard,
    )

    assert result.scene_id == planning_input.scene.scene_id
    prompt = llm.requests[0].user_prompt
    assert "Validated whiteboard plan" in prompt
    assert "do not duplicate its layout or drawing primitives" in prompt
    assert '"scene_id": "scene-001"' in prompt
    assert '"action_type": "draw_arrow"' in prompt
    assert "act-1" in prompt


def test_visual_agent_prompt_includes_script_and_source_context(
    planning_input, script_payload, fake_llm_factory
):
    script = _script(script_payload)
    llm = fake_llm_factory(json.dumps(_plan_payload()))
    agent = VisualDesignAgent(llm)

    agent.generate(_visual_input(planning_input, script))

    prompt = build_visual_prompt(_visual_input(planning_input, script))
    assert VISUAL_PROMPT_VERSION == "1.0.0"
    assert "Example & Visual Agent" in prompt.system_prompt
    assert "empty items array" in prompt.system_prompt
    assert "coordinates" in prompt.system_prompt
    assert "drawing primitives" in prompt.system_prompt
    assert "exact_text" in prompt.system_prompt
    assert "untrusted data" in prompt.user_prompt
    assert "Allowed narration segment_ids: seg-1, seg-2, seg-3" in (
        prompt.user_prompt
    )
    assert "Allowed key_point_ids: kp-1, kp-2" in prompt.user_prompt
    assert "Allowed source_ids: src-1, src-2" in prompt.user_prompt
    assert '"page_number": 12' in prompt.user_prompt
    assert "A neural network is like a football team" in prompt.user_prompt
    assert (
        "Validated whiteboard plan (may be null; do not duplicate its layout "
        "or drawing primitives):\nnull" in prompt.user_prompt
    )


def test_visual_prompt_is_deterministic(planning_input, script_payload):
    visual_input = _visual_input(planning_input, _script(script_payload))

    first = build_visual_prompt(visual_input)
    second = build_visual_prompt(visual_input)

    assert first.system_prompt == second.system_prompt
    assert first.user_prompt == second.user_prompt


def test_visual_agent_prompt_respects_learner_preferences(
    planning_input, script_payload, fake_llm_factory
):
    script = _script(script_payload)
    preferences = PersonalizationBrief(
        audience_level=AudienceLevel.ADVANCED,
        language="ar",
        teaching_style="cooking",
        style_guidance="Use kitchen analogies without changing the source facts.",
    )
    customized_input = dataclasses.replace(
        planning_input,
        preferences=preferences,
    )
    llm = fake_llm_factory(json.dumps(_plan_payload()))
    agent = VisualDesignAgent(llm)

    agent.generate(_visual_input(customized_input, script))

    prompt = llm.requests[0].user_prompt
    assert '"audience_level": "advanced"' in prompt
    assert '"language": "ar"' in prompt
    assert '"teaching_style": "cooking"' in prompt
    assert (
        '"style_guidance": "Use kitchen analogies without changing the source facts."'
        in prompt
    )


def test_visual_agent_accepts_empty_plan(
    planning_input, script_payload, fake_llm_factory
):
    script = _script(script_payload)
    visual_input = _visual_input(planning_input, script)
    agent = _agent(
        fake_llm_factory,
        {"scene_id": "scene-001", "items": []},
    )

    result = agent.generate(visual_input)

    assert result.plan.items == ()
    assert result.scene_id == "scene-001"
    assert result.to_dict()["plan"]["items"] == []
    assert validate_visual_plan(result.plan, visual_input).is_valid
    assert validate_visual_result(result, visual_input).is_valid


def test_visual_agent_preserves_exact_text_verbatim(
    planning_input, script_payload, fake_llm_factory
):
    script = _script(script_payload)
    formula = "  y = 2x + 1  "
    code = "def forward(x):\n    return x"
    payload = _plan_payload(
        items=[_item_payload(exact_text=[formula, code])]
    )
    agent = _agent(fake_llm_factory, payload)

    result = agent.generate(_visual_input(planning_input, script))

    assert result.plan.items[0].exact_text == (formula, code)
    assert result.plan.to_json() == VisualPlan.from_dict(
        result.plan.to_dict()
    ).to_json()


def test_visual_agent_rejects_malformed_json(
    planning_input, script_payload, fake_llm_factory
):
    script = _script(script_payload)
    agent = VisualDesignAgent(fake_llm_factory("this is not json"))

    with pytest.raises(AgentOutputError, match="not valid JSON") as exc_info:
        agent.generate(_visual_input(planning_input, script))

    assert exc_info.value.raw_output == "this is not json"


def test_visual_agent_accepts_fenced_json(
    planning_input, script_payload, fake_llm_factory
):
    script = _script(script_payload)
    fenced = f"```json\n{json.dumps(_plan_payload())}\n```"
    agent = VisualDesignAgent(fake_llm_factory(fenced))

    result = agent.generate(_visual_input(planning_input, script))

    assert result.scene_id == "scene-001"


def test_visual_agent_rejects_missing_required_field(
    planning_input, script_payload, fake_llm_factory
):
    script = _script(script_payload)
    item = _item_payload()
    del item["description"]
    agent = _agent(
        fake_llm_factory,
        _plan_payload(items=[item]),
    )

    with pytest.raises(
        AgentOutputError, match="visual plan contract validation failed"
    ) as exc_info:
        agent.generate(_visual_input(planning_input, script))

    assert "missing required fields: description" in str(exc_info.value)


def test_visual_agent_rejects_invalid_enum(
    planning_input, script_payload, fake_llm_factory
):
    script = _script(script_payload)
    agent = _agent(
        fake_llm_factory,
        _plan_payload(items=[_item_payload(kind="hologram")]),
    )

    with pytest.raises(
        AgentOutputError, match="visual plan contract validation failed"
    ) as exc_info:
        agent.generate(_visual_input(planning_input, script))

    assert "visual_item.kind must be one of" in str(exc_info.value)


def test_visual_agent_rejects_unknown_segment_id(
    planning_input, script_payload, fake_llm_factory
):
    script = _script(script_payload)
    agent = _agent(
        fake_llm_factory,
        _plan_payload(items=[_item_payload(segment_ids=["seg-999"])]),
    )

    with pytest.raises(AgentOutputError) as exc_info:
        agent.generate(_visual_input(planning_input, script))

    details = "\n".join(exc_info.value.details)
    assert "unknown_segment" in details
    assert "seg-999" in details


def test_visual_agent_rejects_unknown_key_point_id(
    planning_input, script_payload, fake_llm_factory
):
    script = _script(script_payload)
    agent = _agent(
        fake_llm_factory,
        _plan_payload(items=[_item_payload(key_point_ids=["kp-999"])]),
    )

    with pytest.raises(AgentOutputError) as exc_info:
        agent.generate(_visual_input(planning_input, script))

    details = "\n".join(exc_info.value.details)
    assert "unknown_key_point" in details
    assert "kp-999" in details


def test_visual_agent_rejects_unknown_source_id(
    planning_input, script_payload, fake_llm_factory
):
    script = _script(script_payload)
    agent = _agent(
        fake_llm_factory,
        _plan_payload(items=[_item_payload(source_ids=["src-999"])]),
    )

    with pytest.raises(AgentOutputError) as exc_info:
        agent.generate(_visual_input(planning_input, script))

    details = "\n".join(exc_info.value.details)
    assert "unknown_source" in details
    assert "src-999" in details


def test_visual_agent_rejects_scene_id_mismatch(
    planning_input, script_payload, fake_llm_factory
):
    script = _script(script_payload)
    agent = _agent(
        fake_llm_factory,
        _plan_payload(scene_id="scene-999"),
    )

    with pytest.raises(AgentOutputError) as exc_info:
        agent.generate(_visual_input(planning_input, script))

    details = "\n".join(exc_info.value.details)
    assert "scene_id_mismatch" in details
    assert "scene-999" in details


def test_visual_agent_wraps_provider_failures(
    planning_input, script_payload, fake_llm_factory
):
    script = _script(script_payload)
    agent = VisualDesignAgent(
        fake_llm_factory(RuntimeError("provider unavailable"))
    )

    with pytest.raises(LLMProviderError, match="provider call failed"):
        agent.generate(_visual_input(planning_input, script))


def test_visual_agent_rejects_invalid_input_type(fake_llm_factory):
    llm = fake_llm_factory()
    agent = VisualDesignAgent(llm)

    with pytest.raises(AgentInputError, match="VisualPlanningInput"):
        agent.generate("not a visual planning input")

    assert llm.requests == []


def test_visual_agent_rejects_invalid_upstream_script_before_provider(
    planning_input, script_payload, fake_llm_factory
):
    invalid = copy.deepcopy(script_payload)
    invalid["key_points"][0]["source_ids"] = ["src-999"]
    script = _script(invalid)
    llm = fake_llm_factory()
    agent = VisualDesignAgent(llm)

    with pytest.raises(AgentInputError) as exc_info:
        agent.generate(_visual_input(planning_input, script))

    details = "\n".join(exc_info.value.details)
    assert "script.unknown_source_ids" in details
    assert "src-999" in details
    assert llm.requests == []


def test_visual_agent_rejects_unsupported_provider_response(
    planning_input, script_payload,
):
    script = _script(script_payload)
    client = _RawResponseClient("not an LLMResponse")
    agent = VisualDesignAgent(client)

    with pytest.raises(
        AgentOutputError, match="unsupported response type"
    ):
        agent.generate(_visual_input(planning_input, script))


def test_visual_validation_reports_reference_errors(
    planning_input, script_payload
):
    script = _script(script_payload)
    visual_input = _visual_input(planning_input, script)
    plan = VisualPlan.from_dict(
        _plan_payload(
            items=[
                _item_payload(
                    segment_ids=["seg-999"],
                    key_point_ids=["kp-999"],
                    source_ids=["src-999"],
                )
            ]
        )
    )

    report = validate_visual_plan(plan, visual_input)

    assert not report.is_valid
    assert {issue.code for issue in report.errors} == {
        "visual.unknown_key_point",
        "visual.unknown_segment",
        "visual.unknown_source",
    }


def test_visual_validation_reports_scene_mismatch(
    planning_input, script_payload
):
    script = _script(script_payload)
    visual_input = _visual_input(planning_input, script)
    plan = VisualPlan.from_dict(_plan_payload(scene_id="scene-999"))

    report = validate_visual_plan(plan, visual_input)

    assert not report.is_valid
    assert {issue.code for issue in report.errors} == {
        "visual.scene_id_mismatch"
    }


def test_visual_validation_accepts_valid_input_plan_and_result(
    planning_input, script_payload
):
    script = _script(script_payload)
    visual_input = _visual_input(planning_input, script)
    plan = VisualPlan.from_dict(_plan_payload())
    result = VisualPlanningResult(scene_id="scene-001", plan=plan)

    assert validate_visual_input(visual_input).is_valid
    assert validate_visual_plan(plan, visual_input).is_valid
    assert validate_visual_result(result, visual_input).is_valid


def test_visual_validation_rejects_wrong_types():
    assert not validate_visual_input("not an input").is_valid
    assert not validate_visual_plan("not a plan", "not an input").is_valid
    assert not validate_visual_result("not a result", "not an input").is_valid


def test_visual_validation_reports_result_scene_mismatch(
    planning_input, script_payload
):
    script = _script(script_payload)
    visual_input = _visual_input(planning_input, script)
    wrong_plan = VisualPlan.from_dict(
        _plan_payload(scene_id="scene-999")
    )
    wrong_result = VisualPlanningResult(
        scene_id="scene-999",
        plan=wrong_plan,
    )

    report = validate_visual_result(wrong_result, visual_input)

    assert not report.is_valid
    codes = {issue.code for issue in report.errors}
    assert "visual_result.scene_id_mismatch" in codes
    assert "visual.scene_id_mismatch" in codes


def test_visual_schema_matches_contract_and_accepts_payloads():
    jsonschema = pytest.importorskip("jsonschema")

    jsonschema.Draft202012Validator.check_schema(VISUAL_PLAN_OUTPUT_SCHEMA)
    assert VISUAL_PLAN_OUTPUT_SCHEMA["title"] == "VisualPlan"
    assert VISUAL_PLAN_OUTPUT_SCHEMA["additionalProperties"] is False
    assert set(VISUAL_PLAN_OUTPUT_SCHEMA["required"]) == {
        "scene_id",
        "items",
    }
    assert VISUAL_PLAN_OUTPUT_SCHEMA["properties"]["schema_version"][
        "enum"
    ] == [VISUAL_PLAN_SCHEMA_VERSION]
    assert set(VISUAL_PLAN_OUTPUT_SCHEMA["properties"]) == {
        "schema_version",
        "scene_id",
        "items",
    }
    assert "minItems" not in VISUAL_PLAN_OUTPUT_SCHEMA["properties"]["items"]

    item_schema = VISUAL_PLAN_OUTPUT_SCHEMA["properties"]["items"]["items"]
    assert item_schema["additionalProperties"] is False
    assert set(item_schema["properties"]) == {
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
    assert set(item_schema["required"]) == {
        "visual_id",
        "purpose",
        "kind",
        "description",
    }
    assert set(item_schema["properties"]["purpose"]["enum"]) == {
        purpose.value for purpose in VisualPurpose
    }
    assert set(item_schema["properties"]["kind"]["enum"]) == {
        kind.value for kind in VisualKind
    }
    assert item_schema["properties"]["description"]["maxLength"] == (
        MAX_VISUAL_DESCRIPTION_LENGTH
    )
    assert item_schema["properties"]["exact_text"]["items"]["maxLength"] == (
        MAX_VISUAL_EXACT_TEXT_LENGTH
    )
    assert item_schema["properties"]["accessibility_notes"]["maxItems"] == (
        MAX_VISUAL_ACCESSIBILITY_NOTES
    )

    jsonschema.validate(_plan_payload(), VISUAL_PLAN_OUTPUT_SCHEMA)
    jsonschema.validate(
        {"scene_id": "scene-001", "items": []},
        VISUAL_PLAN_OUTPUT_SCHEMA,
    )


def test_visual_schema_rejects_runtime_invalid_payloads():
    jsonschema = pytest.importorskip("jsonschema")

    bad_enum = _plan_payload(
        items=[_item_payload(purpose="explain_everything")]
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad_enum, VISUAL_PLAN_OUTPUT_SCHEMA)

    missing_description = _plan_payload()
    del missing_description["items"][0]["description"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(missing_description, VISUAL_PLAN_OUTPUT_SCHEMA)

    rendering_field = _plan_payload()
    rendering_field["items"][0]["coordinates"] = [0.1, 0.2]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(rendering_field, VISUAL_PLAN_OUTPUT_SCHEMA)

    duplicate_segments = _plan_payload(
        items=[_item_payload(segment_ids=["seg-1", "seg-1"])]
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(duplicate_segments, VISUAL_PLAN_OUTPUT_SCHEMA)

    oversized_description = _plan_payload(
        items=[
            _item_payload(
                description="x" * (MAX_VISUAL_DESCRIPTION_LENGTH + 1)
            )
        ]
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            oversized_description, VISUAL_PLAN_OUTPUT_SCHEMA
        )
