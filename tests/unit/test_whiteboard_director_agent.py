"""Unit tests for the Whiteboard Director Agent.

Responsibility: verify structured prompt construction, whiteboard contract
validation, narration anchoring, continuity handling, and typed failures.
Workflow: unit tests with a deterministic fake LLM; no network or credentials.
Inputs: fixtures from ``tests/conftest.py``.
Outputs: pytest assertions only.
Interacts with: ``WhiteboardDirectorAgent``, scene and whiteboard contracts, and
validation.
"""

from __future__ import annotations

import copy
import json

import pytest

from ai_video_generator.agents.scene_planning.errors import (
    AgentInputError,
    AgentOutputError,
    LLMProviderError,
)
from ai_video_generator.agents.whiteboard.whiteboard_director_agent import (
    WhiteboardDirectorAgent,
)
from ai_video_generator.models.scene import SceneScript
from ai_video_generator.whiteboard.plan import WhiteboardDirectionInput, WhiteboardPlan


def _make_agent(fake_llm_factory, payload):
    return WhiteboardDirectorAgent(fake_llm_factory(json.dumps(payload)))


def test_whiteboard_agent_generates_validated_plan(
    planning_input, script_payload, whiteboard_payload, fake_llm_factory
):
    script = SceneScript.from_dict(script_payload)
    llm = fake_llm_factory(json.dumps(whiteboard_payload))
    agent = WhiteboardDirectorAgent(llm)

    plan = agent.generate(
        WhiteboardDirectionInput(planning_input=planning_input, script=script)
    )

    assert isinstance(plan, WhiteboardPlan)
    assert plan.scene_id == "scene-001"
    assert plan.segment_anchors == ("seg-1", "seg-2", "seg-3")
    assert len(llm.requests) == 1
    request = llm.requests[0]
    assert request.response_schema_name == "WhiteboardPlan"
    assert request.response_schema["title"] == "WhiteboardPlan"
    assert request.metadata["agent"] == "whiteboard_director_agent"
    assert request.metadata["scene_id"] == "scene-001"
    assert request.metadata["lesson_id"] == "lesson-001"


def test_whiteboard_agent_generate_for_scene_convenience(
    planning_input, script_payload, whiteboard_payload, fake_llm_factory
):
    script = SceneScript.from_dict(script_payload)
    agent = _make_agent(fake_llm_factory, whiteboard_payload)

    plan = agent.generate_for_scene(planning_input, script)

    assert plan.scene_id == planning_input.scene.scene_id


def test_whiteboard_agent_rejects_unknown_narration_segment(
    planning_input, script_payload, whiteboard_payload, fake_llm_factory
):
    invalid = copy.deepcopy(whiteboard_payload)
    invalid["drawing"]["actions"][0]["segment_id"] = "seg-999"
    script = SceneScript.from_dict(script_payload)
    agent = _make_agent(fake_llm_factory, invalid)

    with pytest.raises(AgentOutputError) as exc_info:
        agent.generate_for_scene(planning_input, script)

    details = "\n".join(exc_info.value.details)
    assert "unknown_segment" in details
    assert "seg-999" in details


def test_whiteboard_agent_rejects_handwriting_cue_for_non_write_action(
    planning_input, script_payload, whiteboard_payload, fake_llm_factory
):
    invalid = copy.deepcopy(whiteboard_payload)
    invalid["handwriting"]["cues"][0]["action_id"] = "act-3"  # draw_arrow
    script = SceneScript.from_dict(script_payload)
    agent = _make_agent(fake_llm_factory, invalid)

    with pytest.raises(AgentOutputError, match="contract validation failed") as exc_info:
        agent.generate_for_scene(planning_input, script)

    assert "must reference a write action" in str(exc_info.value)


def test_whiteboard_agent_rejects_unknown_highlight_target(
    planning_input, script_payload, whiteboard_payload, fake_llm_factory
):
    invalid = copy.deepcopy(whiteboard_payload)
    invalid["highlights"]["cues"][0]["target_action_id"] = "act-999"
    script = SceneScript.from_dict(script_payload)
    agent = _make_agent(fake_llm_factory, invalid)

    with pytest.raises(AgentOutputError, match="contract validation failed") as exc_info:
        agent.generate_for_scene(planning_input, script)

    assert "targets unknown action_id" in str(exc_info.value)


def test_whiteboard_agent_rejects_continuity_without_previous_plan(
    planning_input, script_payload, whiteboard_payload, fake_llm_factory
):
    invalid = copy.deepcopy(whiteboard_payload)
    invalid["continuity"] = {
        "mode": "continue_board",
        "carry_over_action_ids": ["act-1"],
        "clear_region_ids": [],
        "notes": [],
    }
    script = SceneScript.from_dict(script_payload)
    agent = _make_agent(fake_llm_factory, invalid)

    with pytest.raises(AgentOutputError) as exc_info:
        agent.generate_for_scene(planning_input, script)

    details = "\n".join(exc_info.value.details)
    assert "continuity_without_previous" in details
    assert "carry_over_without_previous" in details


def test_whiteboard_agent_accepts_continuity_with_previous_plan(
    planning_input, script_payload, whiteboard_payload, fake_llm_factory
):
    previous_payload = copy.deepcopy(whiteboard_payload)
    previous_payload["scene_id"] = "scene-000"
    previous_plan = WhiteboardPlan.from_dict(previous_payload)

    current_payload = copy.deepcopy(whiteboard_payload)
    current_payload["continuity"] = {
        "mode": "continue_board",
        "carry_over_action_ids": ["act-1"],
        "clear_region_ids": [],
        "notes": ["Keep the layers diagram visible."],
    }
    script = SceneScript.from_dict(script_payload)
    llm = fake_llm_factory(json.dumps(current_payload))
    agent = WhiteboardDirectorAgent(llm)

    plan = agent.generate_for_scene(
        planning_input, script, previous_plan=previous_plan
    )

    assert plan.continuity.mode.value == "continue_board"
    assert plan.continuity.carry_over_action_ids == ("act-1",)
    request = llm.requests[0]
    assert "Previous action_ids: act-1, act-2, act-3, act-4" in request.user_prompt
    assert "Previous region_ids: title, main, summary" in request.user_prompt


def test_whiteboard_agent_rejects_invalid_carry_over(
    planning_input, script_payload, whiteboard_payload, fake_llm_factory
):
    previous_payload = copy.deepcopy(whiteboard_payload)
    previous_payload["scene_id"] = "scene-000"
    previous_plan = WhiteboardPlan.from_dict(previous_payload)

    current_payload = copy.deepcopy(whiteboard_payload)
    current_payload["continuity"] = {
        "mode": "continue_board",
        "carry_over_action_ids": ["act-999"],
        "clear_region_ids": [],
        "notes": [],
    }
    script = SceneScript.from_dict(script_payload)
    agent = _make_agent(fake_llm_factory, current_payload)

    with pytest.raises(AgentOutputError) as exc_info:
        agent.generate_for_scene(planning_input, script, previous_plan=previous_plan)

    details = "\n".join(exc_info.value.details)
    assert "unknown_carry_over" in details
    assert "act-999" in details


def test_whiteboard_agent_prompt_contains_script_and_director_rules(
    planning_input, script_payload, fake_llm_factory
):
    script = SceneScript.from_dict(script_payload)
    llm = fake_llm_factory("{}")  # invalid output; we only inspect the request
    agent = WhiteboardDirectorAgent(llm)

    with pytest.raises(AgentOutputError):
        agent.generate_for_scene(planning_input, script)

    request = llm.requests[0]
    assert "Whiteboard Director Agent" in request.system_prompt
    assert "Never include an instructor" in request.system_prompt
    assert "normalized coordinates" in request.system_prompt
    assert "Allowed narration segment_ids: seg-1, seg-2, seg-3" in request.user_prompt
    assert "Validated script (authoritative; do not rewrite its narration)" in (
        request.user_prompt
    )
    assert "seg-1" in request.user_prompt
    assert "no previous plan" not in request.user_prompt.lower()
    assert "Previous whiteboard plan (may be null):\nnull" in request.user_prompt
    assert "If the previous plan is null, use fresh_board continuity" in (
        request.user_prompt
    )


def test_whiteboard_agent_rejects_invalid_direction_input(
    planning_input, script_payload, fake_llm_factory
):
    script = SceneScript.from_dict(script_payload)
    llm = fake_llm_factory()
    agent = WhiteboardDirectorAgent(llm)

    with pytest.raises(AgentInputError):
        agent.generate("not a direction input")
    assert llm.requests == []


def test_whiteboard_agent_wraps_provider_failures(
    planning_input, script_payload, fake_llm_factory
):
    script = SceneScript.from_dict(script_payload)
    agent = WhiteboardDirectorAgent(
        fake_llm_factory(RuntimeError("provider unavailable"))
    )

    with pytest.raises(LLMProviderError, match="provider call failed"):
        agent.generate_for_scene(planning_input, script)


def test_whiteboard_agent_rejects_malformed_json(
    planning_input, script_payload, fake_llm_factory
):
    script = SceneScript.from_dict(script_payload)
    agent = WhiteboardDirectorAgent(fake_llm_factory("not json"))

    with pytest.raises(AgentOutputError, match="not valid JSON"):
        agent.generate_for_scene(planning_input, script)
