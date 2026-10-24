"""Whiteboard highlighting and emphasis contract.

Responsibility: describe marker highlights, underlines, circles, and boxes that
draw attention to previously created board elements.
Workflow: produced by the Whiteboard Director Agent and consumed by scene
packaging and future renderers.
Inputs: highlight IDs, target drawing action IDs, narration segment anchors,
styles, colors, and ordering.
Outputs: validated ``HighlightPlan`` and ``HighlightCue`` objects.
Interacts with: ``whiteboard.drawing_instructions``, ``whiteboard.plan``, and
validation.
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
    optional_text,
    require_enum,
    require_id,
    require_int,
    require_keys,
    require_strictly_increasing,
    require_unique,
)

# Shared with the provider-facing JSON Schema.
MAX_HIGHLIGHT_CUES = 80


class HighlightStyle(str, Enum):
    """Supported emphasis styles."""

    MARKER = "marker"
    UNDERLINE = "underline"
    CIRCLE = "circle"
    BOX = "box"


@dataclass(frozen=True, slots=True)
class HighlightCue(ContractModel):
    """One emphasis action targeting an existing drawing action."""

    highlight_id: str
    target_action_id: str
    segment_id: str
    style: HighlightStyle
    order: int
    color: str | None = None
    duration_seconds: float | None = None

    def __post_init__(self) -> None:
        require_id(self.highlight_id, "highlight.highlight_id")
        require_id(self.target_action_id, "highlight.target_action_id")
        require_id(self.segment_id, "highlight.segment_id")
        if not isinstance(self.style, HighlightStyle):
            object.__setattr__(
                self,
                "style",
                require_enum(self.style, HighlightStyle, "highlight.style"),
            )
        require_int(self.order, "highlight.order", min_value=1)
        optional_text(self.color, "highlight.color", max_length=32)
        optional_number(
            self.duration_seconds,
            "highlight.duration_seconds",
            min_value=0.1,
            max_value=600.0,
        )

    @classmethod
    def from_dict(cls, data: Any) -> "HighlightCue":
        mapping = require_keys(
            data,
            allowed={
                "highlight_id",
                "target_action_id",
                "segment_id",
                "style",
                "order",
                "color",
                "duration_seconds",
            },
            required={
                "highlight_id",
                "target_action_id",
                "segment_id",
                "style",
                "order",
            },
            field="highlight_cue",
        )
        return cls(
            highlight_id=require_id(
                mapping["highlight_id"], "highlight.highlight_id"
            ),
            target_action_id=require_id(
                mapping["target_action_id"], "highlight.target_action_id"
            ),
            segment_id=require_id(mapping["segment_id"], "highlight.segment_id"),
            style=require_enum(
                mapping["style"], HighlightStyle, "highlight.style"
            ),
            order=require_int(mapping["order"], "highlight.order", min_value=1),
            color=optional_text(
                mapping.get("color"), "highlight.color", max_length=32
            ),
            duration_seconds=optional_number(
                mapping.get("duration_seconds"),
                "highlight.duration_seconds",
                min_value=0.1,
                max_value=600.0,
            ),
        )


@dataclass(frozen=True, slots=True)
class HighlightPlan(ContractModel):
    """Ordered highlights for one scene; may be empty when no emphasis is needed."""

    cues: tuple[HighlightCue, ...]

    def __post_init__(self) -> None:
        cues = ensure_sequence(self.cues, "highlights.cues")
        if len(cues) > MAX_HIGHLIGHT_CUES:
            raise ContractError(
                f"highlights.cues must contain at most {MAX_HIGHLIGHT_CUES} cues"
            )
        if not all(isinstance(cue, HighlightCue) for cue in cues):
            raise ContractError("highlights.cues must contain HighlightCue objects")
        object.__setattr__(self, "cues", tuple(cues))
        require_unique(
            [cue.highlight_id for cue in self.cues], "highlights.cues.highlight_id"
        )
        require_unique(
            [cue.order for cue in self.cues], "highlights.cues.order"
        )
        require_strictly_increasing(
            [cue.order for cue in self.cues], "highlights.cues.order"
        )

    @classmethod
    def from_dict(cls, data: Any) -> "HighlightPlan":
        mapping = require_keys(
            data,
            allowed={"cues"},
            required={"cues"},
            field="highlights",
        )
        cue_items = ensure_sequence(mapping["cues"], "highlights.cues")
        return cls(cues=tuple(HighlightCue.from_dict(item) for item in cue_items))


__all__ = [
    "HighlightCue",
    "HighlightPlan",
    "HighlightStyle",
    "MAX_HIGHLIGHT_CUES",
]
