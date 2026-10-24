"""Combined whiteboard plan contract.

Responsibility: compose board layout, drawing actions, handwriting cues,
highlights, and continuity decisions into one validated scene-level plan.
Workflow: produced by the Whiteboard Director Agent and consumed by scene
packaging, validation, and future renderers.
Inputs: validated components from the whiteboard contract modules.
Outputs: ``WhiteboardPlan`` and the structured ``WhiteboardDirectionInput``.
Interacts with: ``models.scene``, all whiteboard contract modules, prompts,
validation, and downstream scene packaging.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models.base import (
    ContractError,
    ContractModel,
    ensure_sequence,
    require_keys,
    require_id,
    require_strictly_increasing,
    require_text,
    require_text_tuple,
)
from ..models.scene import ScenePlanningInput, SceneScript
from .board_layout import BoardLayout
from .drawing_instructions import DrawingActionType, DrawingPlan
from .handwriting_plan import HandwritingMode, HandwritingPlan
from .highlighting import HighlightPlan
from .visual_continuity import ContinuityPlan

WHITEBOARD_PLAN_SCHEMA_VERSION = "1.0"

SUPPORTED_WHITEBOARD_PLAN_SCHEMA_VERSIONS = frozenset(
    {WHITEBOARD_PLAN_SCHEMA_VERSION}
)

_GEOMETRY_EPSILON = 1e-6


@dataclass(frozen=True, slots=True)
class WhiteboardPlan(ContractModel):
    """Complete whiteboard direction for one scene.

    Drawing and highlighting are anchored to narration segments. The plan does
    not include absolute timestamps, provider prompts, media files, or any
    on-screen human representation.
    """

    scene_id: str
    layout: BoardLayout
    drawing: DrawingPlan
    handwriting: HandwritingPlan
    highlights: HighlightPlan
    continuity: ContinuityPlan
    presentation_notes: tuple[str, ...] = ()
    schema_version: str = WHITEBOARD_PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version not in SUPPORTED_WHITEBOARD_PLAN_SCHEMA_VERSIONS:
            raise ContractError(
                f"whiteboard.schema_version must be one of: "
                f"{', '.join(sorted(SUPPORTED_WHITEBOARD_PLAN_SCHEMA_VERSIONS))}"
            )
        require_id(self.scene_id, "whiteboard.scene_id")
        if not isinstance(self.layout, BoardLayout):
            raise ContractError("whiteboard.layout must be a BoardLayout")
        if not isinstance(self.drawing, DrawingPlan):
            raise ContractError("whiteboard.drawing must be a DrawingPlan")
        if not isinstance(self.handwriting, HandwritingPlan):
            raise ContractError("whiteboard.handwriting must be a HandwritingPlan")
        if not isinstance(self.highlights, HighlightPlan):
            raise ContractError("whiteboard.highlights must be a HighlightPlan")
        if not isinstance(self.continuity, ContinuityPlan):
            raise ContractError("whiteboard.continuity must be a ContinuityPlan")
        notes = require_text_tuple(
            self.presentation_notes,
            "whiteboard.presentation_notes",
            max_items=20,
            max_length=1_000,
        )
        object.__setattr__(self, "presentation_notes", notes)

        for action in self.drawing.actions:
            if action.region_id not in self.layout.region_ids:
                raise ContractError(
                    f"drawing action '{action.action_id}' references unknown "
                    f"region_id '{action.region_id}'"
                )
            region = self.layout.region_by_id(action.region_id)
            for point in action.points:
                inside = (
                    region.x - _GEOMETRY_EPSILON
                    <= point.x
                    <= region.x + region.width + _GEOMETRY_EPSILON
                    and region.y - _GEOMETRY_EPSILON
                    <= point.y
                    <= region.y + region.height + _GEOMETRY_EPSILON
                )
                if not inside:
                    raise ContractError(
                        f"drawing action '{action.action_id}' point "
                        f"({point.x}, {point.y}) lies outside its region_id "
                        f"'{action.region_id}'"
                    )

        for cue in self.handwriting.cues:
            action = self.drawing.action_by_id(cue.action_id)
            if action.action_type is DrawingActionType.WRITE_EQUATION:
                if cue.mode is not HandwritingMode.EQUATION:
                    raise ContractError(
                        f"handwriting cue '{cue.cue_id}' must use equation mode for "
                        f"equation action '{action.action_id}'"
                    )
            elif action.action_type is DrawingActionType.WRITE_TEXT:
                if cue.mode is HandwritingMode.EQUATION:
                    raise ContractError(
                        f"handwriting cue '{cue.cue_id}' cannot use equation mode for "
                        f"text action '{action.action_id}'"
                    )
            else:
                raise ContractError(
                    f"handwriting cue '{cue.cue_id}' must reference a write action, "
                    f"got '{action.action_type.value}'"
                )
        require_strictly_increasing(
            [
                self.drawing.action_by_id(cue.action_id).order
                for cue in self.handwriting.cues
            ],
            "whiteboard.handwriting.cues.action_order",
        )

        for cue in self.highlights.cues:
            if cue.target_action_id not in self.drawing.action_ids:
                raise ContractError(
                    f"highlight '{cue.highlight_id}' targets unknown action_id "
                    f"'{cue.target_action_id}'"
                )
            target = self.drawing.action_by_id(cue.target_action_id)
            if target.action_type is DrawingActionType.ERASE:
                raise ContractError(
                    f"highlight '{cue.highlight_id}' cannot target erase action "
                    f"'{target.action_id}'"
                )

    @property
    def element_ids(self) -> tuple[str, ...]:
        return self.drawing.action_ids

    @property
    def segment_anchors(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    action.segment_id
                    for action in self.drawing.actions
                }
                | {cue.segment_id for cue in self.highlights.cues}
            )
        )

    @property
    def write_action_ids(self) -> tuple[str, ...]:
        return self.drawing.write_action_ids

    @classmethod
    def from_dict(cls, data: Any) -> "WhiteboardPlan":
        mapping = require_keys(
            data,
            allowed={
                "scene_id",
                "layout",
                "drawing",
                "handwriting",
                "highlights",
                "continuity",
                "presentation_notes",
                "schema_version",
            },
            required={
                "scene_id",
                "layout",
                "drawing",
                "handwriting",
                "highlights",
                "continuity",
            },
            field="whiteboard",
        )
        notes = ensure_sequence(
            mapping.get("presentation_notes", []),
            "whiteboard.presentation_notes",
        )
        return cls(
            scene_id=require_id(mapping["scene_id"], "whiteboard.scene_id"),
            layout=BoardLayout.from_dict(mapping["layout"]),
            drawing=DrawingPlan.from_dict(mapping["drawing"]),
            handwriting=HandwritingPlan.from_dict(mapping["handwriting"]),
            highlights=HighlightPlan.from_dict(mapping["highlights"]),
            continuity=ContinuityPlan.from_dict(mapping["continuity"]),
            presentation_notes=require_text_tuple(
                notes,
                "whiteboard.presentation_notes",
                max_items=20,
                max_length=1_000,
            ),
            schema_version=require_text(
                mapping.get("schema_version", WHITEBOARD_PLAN_SCHEMA_VERSION),
                "whiteboard.schema_version",
                max_length=32,
            ),
        )


@dataclass(frozen=True, slots=True)
class WhiteboardDirectionInput(ContractModel):
    """Structured input for a Whiteboard Director Agent run."""

    planning_input: ScenePlanningInput
    script: SceneScript
    previous_plan: WhiteboardPlan | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.planning_input, ScenePlanningInput):
            raise ContractError("direction_input.planning_input must be ScenePlanningInput")
        if not isinstance(self.script, SceneScript):
            raise ContractError("direction_input.script must be a SceneScript")
        if self.script.scene_id != self.planning_input.scene.scene_id:
            raise ContractError(
                "direction_input.script.scene_id must match planning_input.scene.scene_id"
            )
        if self.previous_plan is not None and not isinstance(
            self.previous_plan, WhiteboardPlan
        ):
            raise ContractError(
                "direction_input.previous_plan must be a WhiteboardPlan or None"
            )


__all__ = [
    "WHITEBOARD_PLAN_SCHEMA_VERSION",
    "WhiteboardDirectionInput",
    "WhiteboardPlan",
]
