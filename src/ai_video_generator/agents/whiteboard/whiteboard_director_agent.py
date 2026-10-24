"""Whiteboard Director Agent implementation.

Responsibility: turn a validated ``SceneScript`` into a validated
``WhiteboardPlan`` containing board layout, drawing actions, progressive
handwriting, highlights, and continuity decisions.
Workflow: scene planning after the Script Agent; before scene packaging and
audio/video generation.
Inputs: a ``WhiteboardDirectionInput`` (scene scope, validated script, optional
previous whiteboard plan) and an ``LLMClient``.
Outputs: a validated ``WhiteboardPlan``; failures are typed ``ScenePlanningError``
values.
Interacts with: Script Agent output, prompt/schema builders, whiteboard contracts,
validation, and the downstream Scene Packager.
"""

from __future__ import annotations

from ...models.base import ContractError, require_number
from ...models.scene import ScenePlanningInput, SceneScript
from ...whiteboard.plan import WhiteboardDirectionInput, WhiteboardPlan
from ..scene_planning.errors import (
    AgentInputError,
    AgentOutputError,
    LLMProviderError,
    LLMResponseFormatError,
    ScenePlanningError,
)
from ..scene_planning.llm import LLMClient, LLMRequest, LLMResponse, parse_json_object
from ..scene_planning.prompts import (
    WHITEBOARD_PROMPT_VERSION,
    build_whiteboard_prompt,
)
from ..scene_planning.schemas import WHITEBOARD_PLAN_OUTPUT_SCHEMA
from ..scene_planning.validation import (
    validate_planning_input,
    validate_script,
    validate_whiteboard,
)


class WhiteboardDirectorAgent:
    """Generate and validate whiteboard direction for one scene."""

    agent_name = "whiteboard_director_agent"
    prompt_version = WHITEBOARD_PROMPT_VERSION

    def __init__(self, llm_client: LLMClient, *, temperature: float = 0.2) -> None:
        if not hasattr(llm_client, "generate"):
            raise TypeError("llm_client must implement generate(request) -> LLMResponse")
        self._llm_client = llm_client
        self._temperature = require_number(
            temperature,
            "whiteboard_director_agent.temperature",
            min_value=0.0,
            max_value=2.0,
        )

    def generate(self, direction_input: WhiteboardDirectionInput) -> WhiteboardPlan:
        """Generate a validated whiteboard plan for ``direction_input``.

        Raises:
            AgentInputError: the scene scope or upstream script is inconsistent.
            LLMProviderError: the provider call failed.
            AgentOutputError: the response was malformed or failed validation.
        """
        if not isinstance(direction_input, WhiteboardDirectionInput):
            raise AgentInputError(
                "direction_input must be a WhiteboardDirectionInput"
            )

        planning_input = direction_input.planning_input
        script = direction_input.script

        input_report = validate_planning_input(planning_input)
        if not input_report.is_valid:
            raise AgentInputError(
                "scene-planning input failed validation",
                details=tuple(
                    f"{issue.code}: {issue.message}" for issue in input_report.errors
                ),
            )
        script_report = validate_script(script, planning_input)
        if not script_report.is_valid:
            raise AgentInputError(
                "upstream script failed validation",
                details=tuple(
                    f"{issue.code}: {issue.message}" for issue in script_report.errors
                ),
            )

        prompt = build_whiteboard_prompt(
            planning_input,
            script,
            direction_input.previous_plan,
        )
        request = LLMRequest(
            system_prompt=prompt.system_prompt,
            user_prompt=prompt.user_prompt,
            response_schema_name="WhiteboardPlan",
            response_schema=WHITEBOARD_PLAN_OUTPUT_SCHEMA,
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
                f"whiteboard provider call failed for scene "
                f"'{planning_input.scene.scene_id}'"
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
            plan = WhiteboardPlan.from_dict(payload)
        except ContractError as exc:
            raise AgentOutputError(
                self.agent_name,
                f"whiteboard contract validation failed: {exc}",
                raw_output=response.content,
            ) from exc

        output_report = validate_whiteboard(
            plan,
            script,
            planning_input,
            direction_input.previous_plan,
        )
        if not output_report.is_valid:
            raise AgentOutputError(
                self.agent_name,
                "whiteboard plan failed semantic validation",
                details=tuple(
                    f"{issue.code}: {issue.message}"
                    for issue in output_report.errors
                ),
                raw_output=response.content,
            )
        return plan

    # Convenience wrapper for callers that already hold the two upstream objects.
    def generate_for_scene(
        self,
        planning_input: ScenePlanningInput,
        script: SceneScript,
        *,
        previous_plan: WhiteboardPlan | None = None,
    ) -> WhiteboardPlan:
        """Build the structured input and delegate to :meth:`generate`."""
        return self.generate(
            WhiteboardDirectionInput(
                planning_input=planning_input,
                script=script,
                previous_plan=previous_plan,
            )
        )


__all__ = ["WhiteboardDirectorAgent"]
