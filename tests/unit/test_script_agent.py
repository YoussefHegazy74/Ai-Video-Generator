"""Unit tests for the Script Agent.

Responsibility: verify prompt construction, structured response parsing, source
fidelity, scene identity, and typed failure behavior.
Workflow: unit tests with a deterministic fake LLM; no network or credentials.
Inputs: fixtures from ``tests/conftest.py``.
Outputs: pytest assertions only.
Interacts with: ``ScriptAgent``, scene contracts, and validation.
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
from ai_video_generator.agents.scripting.script_agent import ScriptAgent
from ai_video_generator.models.scene import SceneScript


def test_script_agent_generates_validated_script(
    planning_input, script_payload, fake_llm_factory
):
    llm = fake_llm_factory(json.dumps(script_payload))
    agent = ScriptAgent(llm)

    script = agent.generate(planning_input)

    assert isinstance(script, SceneScript)
    assert script.scene_id == "scene-001"
    assert script.estimated_duration_seconds == 30.0
    assert script.cited_source_ids == ("src-1", "src-2")
    assert len(llm.requests) == 1
    request = llm.requests[0]
    assert request.response_schema_name == "SceneScript"
    assert request.response_schema["title"] == "SceneScript"
    assert request.metadata["agent"] == "script_agent"
    assert request.metadata["scene_id"] == "scene-001"
    assert request.metadata["lesson_id"] == "lesson-001"
    assert "Script Agent" in request.system_prompt
    assert "10-20" in request.user_prompt
    assert "22-24" in request.user_prompt
    assert "src-1" in request.user_prompt
    assert "src-2" in request.user_prompt
    assert "football" in request.user_prompt
    assert "Beginner" not in request.user_prompt  # enum value is serialized as beginner


def test_script_agent_rejects_unknown_source_id(
    planning_input, script_payload, fake_llm_factory
):
    invalid = copy.deepcopy(script_payload)
    invalid["key_points"][0]["source_ids"] = ["src-999"]
    agent = ScriptAgent(fake_llm_factory(json.dumps(invalid)))

    with pytest.raises(AgentOutputError) as exc_info:
        agent.generate(planning_input)

    assert "unknown_source_ids" in "\n".join(exc_info.value.details)
    assert "src-999" in "\n".join(exc_info.value.details)


def test_script_agent_rejects_scene_id_mismatch(
    planning_input, script_payload, fake_llm_factory
):
    invalid = copy.deepcopy(script_payload)
    invalid["scene_id"] = "scene-999"
    agent = ScriptAgent(fake_llm_factory(json.dumps(invalid)))

    with pytest.raises(AgentOutputError) as exc_info:
        agent.generate(planning_input)

    assert "scene_id_mismatch" in "\n".join(exc_info.value.details)


def test_script_agent_rejects_malformed_json(planning_input, fake_llm_factory):
    agent = ScriptAgent(fake_llm_factory("this is not json"))
    with pytest.raises(AgentOutputError, match="not valid JSON"):
        agent.generate(planning_input)


def test_script_agent_wraps_provider_failures(planning_input, fake_llm_factory):
    agent = ScriptAgent(fake_llm_factory(RuntimeError("provider unavailable")))
    with pytest.raises(LLMProviderError, match="provider call failed"):
        agent.generate(planning_input)


def test_script_agent_does_not_call_provider_for_invalid_input(
    planning_input, fake_llm_factory
):
    llm = fake_llm_factory()
    agent = ScriptAgent(llm)
    with pytest.raises(AgentInputError):
        agent.generate("not a planning input")
    assert llm.requests == []


def test_script_agent_rejects_invalid_contract_structure(
    planning_input, script_payload, fake_llm_factory
):
    invalid = copy.deepcopy(script_payload)
    invalid["segments"][0]["key_point_ids"] = ["kp-missing"]
    agent = ScriptAgent(fake_llm_factory(json.dumps(invalid)))

    with pytest.raises(AgentOutputError, match="contract validation failed"):
        agent.generate(planning_input)


def test_script_agent_accepts_fenced_json(
    planning_input, script_payload, fake_llm_factory
):
    fenced = f"```json\n{json.dumps(script_payload)}\n```"
    agent = ScriptAgent(fake_llm_factory(fenced))
    script = agent.generate(planning_input)
    assert script.scene_id == "scene-001"
