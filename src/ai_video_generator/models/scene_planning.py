"""Combined scene-planning artifact for downstream packaging.

Responsibility: bundle the upstream scene brief, validated script, and whiteboard
plan into one contract consumed by the Scene Packager and validation layers.
Workflow: assembled after both scene-planning agents finish; it is not itself an
agent or a provider-facing scene package.
Inputs: ``SceneBrief``, ``SceneScript``, and ``WhiteboardPlan``.
Outputs: a validated ``ScenePlanningResult`` that retains source provenance.
Interacts with: ``models.scene``, ``whiteboard.plan``, validation, and the
downstream scene packaging component. Provider-specific packaging is explicitly
out of scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import ContractError, ContractModel, require_id, require_keys, require_text
from .lesson_plan import SceneBrief
from .scene import SceneScript
from ..whiteboard.plan import WhiteboardPlan

SCENE_PLANNING_RESULT_SCHEMA_VERSION = "1.0"

SUPPORTED_SCENE_PLANNING_RESULT_SCHEMA_VERSIONS = frozenset(
    {SCENE_PLANNING_RESULT_SCHEMA_VERSION}
)


@dataclass(frozen=True, slots=True)
class ScenePlanningResult(ContractModel):
    """Combined, source-preserving handoff from scene planning to packaging."""

    lesson_id: str
    scene: SceneBrief
    script: SceneScript
    whiteboard: WhiteboardPlan
    schema_version: str = SCENE_PLANNING_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version not in SUPPORTED_SCENE_PLANNING_RESULT_SCHEMA_VERSIONS:
            raise ContractError(
                f"scene_planning.schema_version must be one of: "
                f"{', '.join(sorted(SUPPORTED_SCENE_PLANNING_RESULT_SCHEMA_VERSIONS))}"
            )
        require_id(self.lesson_id, "scene_planning.lesson_id")
        if not isinstance(self.scene, SceneBrief):
            raise ContractError("scene_planning.scene must be a SceneBrief")
        if not isinstance(self.script, SceneScript):
            raise ContractError("scene_planning.script must be a SceneScript")
        if not isinstance(self.whiteboard, WhiteboardPlan):
            raise ContractError("scene_planning.whiteboard must be a WhiteboardPlan")
        if self.script.scene_id != self.scene.scene_id:
            raise ContractError(
                "scene_planning.script.scene_id must match scene_planning.scene.scene_id"
            )
        if self.whiteboard.scene_id != self.scene.scene_id:
            raise ContractError(
                "scene_planning.whiteboard.scene_id must match "
                "scene_planning.scene.scene_id"
            )

        script_segments = {segment.segment_id for segment in self.script.segments}
        for anchor in self.whiteboard.segment_anchors:
            if anchor not in script_segments:
                raise ContractError(
                    f"scene_planning whiteboard references unknown narration "
                    f"segment_id '{anchor}'"
                )

        allowed_source_ids = set(self.scene.source_ids)
        for point in self.script.key_points:
            unknown = set(point.source_ids) - allowed_source_ids
            if unknown:
                raise ContractError(
                    f"scene_planning key point '{point.key_point_id}' cites "
                    "source_id(s) that are not present in the scene: "
                    + ", ".join(sorted(unknown))
                )

    @classmethod
    def from_dict(cls, data: Any) -> "ScenePlanningResult":
        mapping = require_keys(
            data,
            allowed={
                "lesson_id",
                "scene",
                "script",
                "whiteboard",
                "schema_version",
            },
            required={"lesson_id", "scene", "script", "whiteboard"},
            field="scene_planning",
        )
        return cls(
            lesson_id=require_id(mapping["lesson_id"], "scene_planning.lesson_id"),
            scene=SceneBrief.from_dict(mapping["scene"]),
            script=SceneScript.from_dict(mapping["script"]),
            whiteboard=WhiteboardPlan.from_dict(mapping["whiteboard"]),
            schema_version=require_text(
                mapping.get(
                    "schema_version", SCENE_PLANNING_RESULT_SCHEMA_VERSION
                ),
                "scene_planning.schema_version",
                max_length=32,
            ),
        )


__all__ = [
    "SCENE_PLANNING_RESULT_SCHEMA_VERSION",
    "ScenePlanningResult",
]
