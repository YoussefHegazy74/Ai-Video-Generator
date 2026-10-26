"""Example & Visual Agent implementation.

Responsibility: turn validated scene-planning context into a scene-scoped
``VisualPlanningResult`` containing a pedagogically grounded ``VisualPlan``.
Workflow: scene planning after the Script Agent and, when available, the
Whiteboard Director Agent; before scene packaging and rendering.
Inputs: a ``VisualPlanningInput`` (scene scope, learner preferences, source
excerpts, validated script, optional whiteboard plan) and an ``LLMClient``.
Outputs: a validated ``VisualPlanningResult``; failures are typed
``ScenePlanningError`` values.
Interacts with: the prompt/schema/validation modules in this package, the shared
scene-planning LLM boundary and errors, and the downstream Scene Packager.

The agent owns teaching-visual selection only. It does not own whiteboard layout,
drawing primitives, timing, rendering, packaging, asset storage, or provider
prompts. It makes exactly one provider call and does not retry.
"""

from __future__ import annotations

from ...models.base import ContractError, require_number
from ...models.scene import ScenePlanningInput, SceneScript
from ...models.visual_plan import VisualPlan
from ...models.visual_planning import (
    VisualPlanningInput,
    VisualPlanningResult,
)
from ...whiteboard.plan import WhiteboardPlan
from ..scene_planning.errors import (
    AgentInputError,
    AgentOutputError,
    LLMProviderError,
    LLMResponseFormatError,
    ScenePlanningError,
)
from ..scene_planning.llm import (
    LLMClient,
    LLMRequest,
    LLMResponse,
    parse_json_object,
)
from .prompts import VISUAL_PROMPT_VERSION, build_visual_prompt
from .schemas import VISUAL_PLAN_OUTPUT_SCHEMA
from .validation import (
    validate_visual_input,
    validate_visual_plan,
    validate_visual_result,
)


class VisualDesignAgent:
    """Generate and validate a teaching-visual plan for one scene."""

    agent_name = "visual_design_agent"
    prompt_version = VISUAL_PROMPT_VERSION

    def __init__(self, llm_client: LLMClient, *, temperature: float = 0.2) -> None:
        if not hasattr(llm_client, "generate"):
            raise TypeError(
                "llm_client must implement generate(request) -> LLMResponse"
            )
        self._llm_client = llm_client
        self._temperature = require_number(
            temperature,
            "visual_design_agent.temperature",
            min_value=0.0,
            max_value=2.0,
        )

    def generate(self, visual_input: VisualPlanningInput) -> VisualPlanningResult:
        """Generate a validated visual plan for ``visual_input``.

        Raises:
            AgentInputError: the scene scope or upstream script is inconsistent.
            LLMProviderError: the provider call failed.
            AgentOutputError: the response was malformed or failed validation.
        """
        if not isinstance(visual_input, VisualPlanningInput):
            raise AgentInputError(
                "visual_input must be a VisualPlanningInput"
            )

        input_report = validate_visual_input(visual_input)
        if not input_report.is_valid:
            raise AgentInputError(
                "visual-planning input failed validation",
                details=tuple(
                    f"{issue.code}: {issue.message}"
                    for issue in input_report.errors
                ),
            )

        prompt = build_visual_prompt(visual_input)
        request = LLMRequest(
            system_prompt=prompt.system_prompt,
            user_prompt=prompt.user_prompt,
            response_schema_name="VisualPlan",
            response_schema=VISUAL_PLAN_OUTPUT_SCHEMA,
            temperature=self._temperature,
            metadata={
                "agent": self.agent_name,
                "prompt_version": self.prompt_version,
                "lesson_id": visual_input.planning_input.lesson_id,
                "scene_id": visual_input.planning_input.scene.scene_id,
            },
        )

        try:
            response = self._llm_client.generate(request)
        except ScenePlanningError:
            raise
        except Exception as exc:  # provider adapters should raise typed errors
            raise LLMProviderError(
                "visual provider call failed for scene "
                f"'{visual_input.planning_input.scene.scene_id}'"
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
            plan = VisualPlan.from_dict(payload)
        except ContractError as exc:
            raise AgentOutputError(
                self.agent_name,
                f"visual plan contract validation failed: {exc}",
                raw_output=response.content,
            ) from exc

        plan_report = validate_visual_plan(plan, visual_input)
        if not plan_report.is_valid:
            raise AgentOutputError(
                self.agent_name,
                "visual plan failed semantic validation",
                details=tuple(
                    f"{issue.code}: {issue.message}"
                    for issue in plan_report.errors
                ),
                raw_output=response.content,
            )

        try:
            result = VisualPlanningResult(
                scene_id=visual_input.planning_input.scene.scene_id,
                plan=plan,
            )
        except ContractError as exc:
            raise AgentOutputError(
                self.agent_name,
                f"visual-planning result contract validation failed: {exc}",
                raw_output=response.content,
            ) from exc

        result_report = validate_visual_result(result, visual_input)
        if not result_report.is_valid:
            raise AgentOutputError(
                self.agent_name,
                "visual-planning result failed semantic validation",
                details=tuple(
                    f"{issue.code}: {issue.message}"
                    for issue in result_report.errors
                ),
                raw_output=response.content,
            )
        return result

    def generate_for_scene(
        self,
        planning_input: ScenePlanningInput,
        script: SceneScript,
        *,
        whiteboard: WhiteboardPlan | None = None,
    ) -> VisualPlanningResult:
        """Build the structured input and delegate to :meth:`generate`."""
        return self.generate(
            VisualPlanningInput(
                planning_input=planning_input,
                script=script,
                whiteboard=whiteboard,
            )
        )


__all__ = ["VisualDesignAgent"]
