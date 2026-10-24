"""Script Agent implementation.

Responsibility: produce a validated, source-grounded ``SceneScript`` for one
planned scene by calling an injected structured LLM client.
Workflow: scene and script planning; runs after lesson planning and before the
Whiteboard Director Agent.
Inputs: a ``ScenePlanningInput`` (scene brief, selected pages, source excerpts,
personalization, target duration) and an ``LLMClient``.
Outputs: a validated ``SceneScript`` with narration segments, key points, source
citations, and delivery notes; failures are typed ``ScenePlanningError`` values.
Interacts with: planning/personalization inputs, prompt and schema builders,
validation, the LLM boundary, and the Whiteboard Director Agent.
"""

from __future__ import annotations

from ...models.base import ContractError, require_number
from ...models.scene import ScenePlanningInput, SceneScript
from ..scene_planning.errors import (
    AgentInputError,
    AgentOutputError,
    LLMProviderError,
    LLMResponseFormatError,
    ScenePlanningError,
)
from ..scene_planning.llm import LLMClient, LLMRequest, LLMResponse, parse_json_object
from ..scene_planning.prompts import SCRIPT_PROMPT_VERSION, build_script_prompt
from ..scene_planning.schemas import SCENE_SCRIPT_OUTPUT_SCHEMA
from ..scene_planning.validation import validate_planning_input, validate_script


class ScriptAgent:
    """Generate and validate one scene script using a structured LLM client."""

    agent_name = "script_agent"
    prompt_version = SCRIPT_PROMPT_VERSION

    def __init__(self, llm_client: LLMClient, *, temperature: float = 0.2) -> None:
        if not hasattr(llm_client, "generate"):
            raise TypeError("llm_client must implement generate(request) -> LLMResponse")
        self._llm_client = llm_client
        self._temperature = require_number(
            temperature, "script_agent.temperature", min_value=0.0, max_value=2.0
        )

    def generate(self, planning_input: ScenePlanningInput) -> SceneScript:
        """Generate a validated script for ``planning_input``.

        Raises:
            AgentInputError: the scene scope is inconsistent.
            LLMProviderError: the provider call failed.
            AgentOutputError: the response was malformed or failed validation.
        """
        if not isinstance(planning_input, ScenePlanningInput):
            raise AgentInputError("planning_input must be a ScenePlanningInput")

        input_report = validate_planning_input(planning_input)
        if not input_report.is_valid:
            raise AgentInputError(
                "scene-planning input failed validation",
                details=tuple(
                    f"{issue.code}: {issue.message}" for issue in input_report.errors
                ),
            )

        prompt = build_script_prompt(planning_input)
        request = LLMRequest(
            system_prompt=prompt.system_prompt,
            user_prompt=prompt.user_prompt,
            response_schema_name="SceneScript",
            response_schema=SCENE_SCRIPT_OUTPUT_SCHEMA,
            temperature=self._temperature,
            metadata={
                "agent": self.agent_name,
                "prompt_version": self.prompt_version,
                "lesson_id": planning_input.lesson_id,
                "scene_id": planning_input.scene.scene_id,
            },
        )

        try:
            response = self._llm_client.generate(request)
        except ScenePlanningError:
            raise
        except Exception as exc:  # provider adapters should raise typed errors
            raise LLMProviderError(
                f"script provider call failed for scene '{planning_input.scene.scene_id}'"
            ) from exc

        if not isinstance(response, LLMResponse):
            raise AgentOutputError(
                self.agent_name,
                "provider returned an unsupported response type",
            )

        try:
            payload = parse_json_object(response.content)
        except LLMResponseFormatError as exc:
            raise AgentOutputError(
                self.agent_name,
                str(exc),
                raw_output=response.content,
            ) from exc

        try:
            script = SceneScript.from_dict(payload)
        except ContractError as exc:
            raise AgentOutputError(
                self.agent_name,
                f"script contract validation failed: {exc}",
                raw_output=response.content,
            ) from exc

        output_report = validate_script(script, planning_input)
        if not output_report.is_valid:
            raise AgentOutputError(
                self.agent_name,
                "script failed semantic validation",
                details=tuple(
                    f"{issue.code}: {issue.message}"
                    for issue in output_report.errors
                ),
                raw_output=response.content,
            )
        return script


__all__ = ["ScriptAgent"]
