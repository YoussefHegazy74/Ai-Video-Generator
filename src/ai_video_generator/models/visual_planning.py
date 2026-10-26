"""Scene-scoped input and result wrappers for visual planning.

Responsibility: compose the existing scene-planning contracts needed by a future
Example & Visual Agent run and wrap the resulting visual plan as a scene-scoped
artifact.
Workflow: the visual-planning input is assembled from an upstream
``ScenePlanningInput``, the validated ``SceneScript``, and an optional existing
``WhiteboardPlan``; the result carries the ``VisualPlan`` for that scene.
Inputs: existing scene, script, and whiteboard contracts plus a visual plan.
Outputs: immutable ``VisualPlanningInput`` and ``VisualPlanningResult`` objects
with stable JSON serialization.
Interacts with: ``models.scene``, ``models.visual_plan``, ``whiteboard.plan``,
and downstream scene packaging.

This module intentionally does not duplicate scene briefs, source excerpts,
personalization, script segments, or whiteboard primitives. It also carries no
LLM request/response fields, agent execution state, asset storage, provider
prompts, or rendering instructions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import (
    ContractError,
    ContractModel,
    require_id,
    require_keys,
    require_text,
)
from .scene import ScenePlanningInput, SceneScript
from .visual_plan import VisualPlan
from ..whiteboard.plan import WhiteboardPlan

VISUAL_PLANNING_RESULT_SCHEMA_VERSION = "1.0"

SUPPORTED_VISUAL_PLANNING_RESULT_SCHEMA_VERSIONS = frozenset(
    {VISUAL_PLANNING_RESULT_SCHEMA_VERSION}
)


@dataclass(frozen=True, slots=True)
class VisualPlanningInput(ContractModel):
    """Structured input for one future Example & Visual Agent run.

    Personalization, selected pages, and source excerpts are supplied through
    ``planning_input``; its ``preferences`` already carry the learner's teaching
    style and guidance. No separate style contract exists yet.
    """

    planning_input: ScenePlanningInput
    script: SceneScript
    whiteboard: WhiteboardPlan | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.planning_input, ScenePlanningInput):
            raise ContractError(
                "visual_input.planning_input must be a ScenePlanningInput"
            )
        if not isinstance(self.script, SceneScript):
            raise ContractError("visual_input.script must be a SceneScript")
        if self.script.scene_id != self.planning_input.scene.scene_id:
            raise ContractError(
                "visual_input.script.scene_id must match "
                "visual_input.planning_input.scene.scene_id"
            )
        if self.whiteboard is not None:
            if not isinstance(self.whiteboard, WhiteboardPlan):
                raise ContractError(
                    "visual_input.whiteboard must be a WhiteboardPlan or None"
                )
            if (
                self.whiteboard.scene_id
                != self.planning_input.scene.scene_id
            ):
                raise ContractError(
                    "visual_input.whiteboard.scene_id must match "
                    "visual_input.planning_input.scene.scene_id"
                )
            script_segments = {
                segment.segment_id for segment in self.script.segments
            }
            unknown = set(self.whiteboard.segment_anchors) - script_segments
            if unknown:
                raise ContractError(
                    "visual_input.whiteboard references unknown narration "
                    "segment_id(s): " + ", ".join(sorted(unknown))
                )

    @classmethod
    def from_dict(cls, data: Any) -> "VisualPlanningInput":
        mapping = require_keys(
            data,
            allowed={"planning_input", "script", "whiteboard"},
            required={"planning_input", "script"},
            field="visual_input",
        )
        whiteboard_data = mapping.get("whiteboard")
        return cls(
            planning_input=ScenePlanningInput.from_dict(
                mapping["planning_input"]
            ),
            script=SceneScript.from_dict(mapping["script"]),
            whiteboard=(
                None
                if whiteboard_data is None
                else WhiteboardPlan.from_dict(whiteboard_data)
            ),
        )


@dataclass(frozen=True, slots=True)
class VisualPlanningResult(ContractModel):
    """Scene-scoped result produced by the future Example & Visual Agent.

    The plan's own ``scene_id`` must match the result's ``scene_id`` so a plan
    cannot be attached to the wrong scene during assembly.
    """

    scene_id: str
    plan: VisualPlan
    schema_version: str = VISUAL_PLANNING_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            self.schema_version
            not in SUPPORTED_VISUAL_PLANNING_RESULT_SCHEMA_VERSIONS
        ):
            raise ContractError(
                "visual_result.schema_version must be one of: "
                + ", ".join(
                    sorted(
                        SUPPORTED_VISUAL_PLANNING_RESULT_SCHEMA_VERSIONS
                    )
                )
            )
        require_id(self.scene_id, "visual_result.scene_id")
        if not isinstance(self.plan, VisualPlan):
            raise ContractError(
                "visual_result.plan must be a VisualPlan"
            )
        if self.plan.scene_id != self.scene_id:
            raise ContractError(
                "visual_result.scene_id must match "
                "visual_result.plan.scene_id"
            )

    @classmethod
    def from_dict(cls, data: Any) -> "VisualPlanningResult":
        mapping = require_keys(
            data,
            allowed={"scene_id", "plan", "schema_version"},
            required={"scene_id", "plan"},
            field="visual_result",
        )
        return cls(
            scene_id=require_id(
                mapping["scene_id"], "visual_result.scene_id"
            ),
            plan=VisualPlan.from_dict(mapping["plan"]),
            schema_version=require_text(
                mapping.get(
                    "schema_version",
                    VISUAL_PLANNING_RESULT_SCHEMA_VERSION,
                ),
                "visual_result.schema_version",
                max_length=32,
            ),
        )


__all__ = [
    "SUPPORTED_VISUAL_PLANNING_RESULT_SCHEMA_VERSIONS",
    "VISUAL_PLANNING_RESULT_SCHEMA_VERSION",
    "VisualPlanningInput",
    "VisualPlanningResult",
]
