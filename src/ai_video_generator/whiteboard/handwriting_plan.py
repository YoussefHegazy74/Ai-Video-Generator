"""Whiteboard handwriting-plan contract.

Responsibility: describe progressive reveal of written text, equations, and code
by referencing write actions from the drawing plan.
Workflow: produced by the Whiteboard Director Agent after drawing actions are
chosen; consumed by scene packaging and future renderers.
Inputs: drawing action IDs, handwriting modes, reveal order, and optional reveal
durations.
Outputs: validated ``HandwritingPlan`` and ``HandwritingCue`` objects.
Interacts with: ``whiteboard.drawing_instructions``, ``whiteboard.plan``, and
validation. Text itself lives once on the referenced drawing action.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..models.base import (
    ContractError,
    ContractModel,
    ensure_sequence,
    optional_number,
    require_enum,
    require_id,
    require_int,
    require_keys,
    require_strictly_increasing,
    require_unique,
)

# Shared with the provider-facing JSON Schema.
MAX_HANDWRITING_CUES = 80


class HandwritingMode(str, Enum):
    """Presentation style for a writing action."""

    HANDWRITE = "handwrite"
    EQUATION = "equation"
    CODE = "code"


@dataclass(frozen=True, slots=True)
class HandwritingCue(ContractModel):
    """Progressive reveal metadata for one write action."""

    cue_id: str
    action_id: str
    mode: HandwritingMode
    order: int
    reveal_duration_seconds: float | None = None

    def __post_init__(self) -> None:
        require_id(self.cue_id, "handwriting.cue_id")
        require_id(self.action_id, "handwriting.action_id")
        if not isinstance(self.mode, HandwritingMode):
            object.__setattr__(
                self,
                "mode",
                require_enum(self.mode, HandwritingMode, "handwriting.mode"),
            )
        require_int(self.order, "handwriting.order", min_value=1)
        optional_number(
            self.reveal_duration_seconds,
            "handwriting.reveal_duration_seconds",
            min_value=0.1,
            max_value=600.0,
        )

    @classmethod
    def from_dict(cls, data: Any) -> "HandwritingCue":
        mapping = require_keys(
            data,
            allowed={"cue_id", "action_id", "mode", "order", "reveal_duration_seconds"},
            required={"cue_id", "action_id", "mode", "order"},
            field="handwriting_cue",
        )
        return cls(
            cue_id=require_id(mapping["cue_id"], "handwriting.cue_id"),
            action_id=require_id(mapping["action_id"], "handwriting.action_id"),
            mode=require_enum(mapping["mode"], HandwritingMode, "handwriting.mode"),
            order=require_int(mapping["order"], "handwriting.order", min_value=1),
            reveal_duration_seconds=optional_number(
                mapping.get("reveal_duration_seconds"),
                "handwriting.reveal_duration_seconds",
                min_value=0.1,
                max_value=600.0,
            ),
        )


@dataclass(frozen=True, slots=True)
class HandwritingPlan(ContractModel):
    """Ordered handwriting cues for one scene; may be empty for diagram-only scenes."""

    cues: tuple[HandwritingCue, ...]

    def __post_init__(self) -> None:
        cues = ensure_sequence(self.cues, "handwriting.cues")
        if len(cues) > MAX_HANDWRITING_CUES:
            raise ContractError(
                "handwriting.cues must contain at most "
                f"{MAX_HANDWRITING_CUES} cues"
            )
        if not all(isinstance(cue, HandwritingCue) for cue in cues):
            raise ContractError("handwriting.cues must contain HandwritingCue objects")
        object.__setattr__(self, "cues", tuple(cues))
        require_unique(
            [cue.cue_id for cue in self.cues], "handwriting.cues.cue_id"
        )
        require_unique(
            [cue.action_id for cue in self.cues], "handwriting.cues.action_id"
        )
        require_unique(
            [cue.order for cue in self.cues], "handwriting.cues.order"
        )
        require_strictly_increasing(
            [cue.order for cue in self.cues], "handwriting.cues.order"
        )

    @classmethod
    def from_dict(cls, data: Any) -> "HandwritingPlan":
        mapping = require_keys(
            data,
            allowed={"cues"},
            required={"cues"},
            field="handwriting",
        )
        cue_items = ensure_sequence(mapping["cues"], "handwriting.cues")
        return cls(cues=tuple(HandwritingCue.from_dict(item) for item in cue_items))


__all__ = [
    "HandwritingCue",
    "HandwritingMode",
    "HandwritingPlan",
    "MAX_HANDWRITING_CUES",
]
