"""Whiteboard drawing-action contract.

Responsibility: define ordered, typed drawing primitives (writing, equations,
arrows, shapes, paths, erasing) anchored to narration segments.
Workflow: produced by the Whiteboard Director Agent, refined by handwriting and
highlighting plans, then mapped to renderer instructions by scene packaging.
Inputs: action identifiers, action types, narration segment IDs, board region
IDs, normalized points, text, and optional styling.
Outputs: validated ``DrawingPlan`` and ``DrawingAction`` objects.
Interacts with: ``whiteboard.board_layout``, ``whiteboard.handwriting_plan``,
``whiteboard.highlighting``, ``whiteboard.plan``, and validation.
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
    require_number,
    require_strictly_increasing,
    require_text,
    require_unique,
)

_WRITE_ACTION_TYPES = frozenset({"write_text", "write_equation"})

# Shared with the provider-facing JSON Schema and the renderer contract.
MAX_ACTION_POINTS = 20
MAX_DRAWING_ACTIONS = 80
MAX_NON_WRITE_TEXT_LENGTH = 300
MAX_WRITE_TEXT_LENGTH = 2_000


class DrawingActionType(str, Enum):
    """Supported whiteboard drawing primitives."""

    WRITE_TEXT = "write_text"
    WRITE_EQUATION = "write_equation"
    DRAW_LINE = "draw_line"
    DRAW_ARROW = "draw_arrow"
    DRAW_RECTANGLE = "draw_rectangle"
    DRAW_CIRCLE = "draw_circle"
    DRAW_PATH = "draw_path"
    ERASE = "erase"


@dataclass(frozen=True, slots=True)
class Point(ContractModel):
    """A normalized point on the whiteboard canvas."""

    x: float
    y: float

    def __post_init__(self) -> None:
        require_number(self.x, "point.x", min_value=0.0, max_value=1.0)
        require_number(self.y, "point.y", min_value=0.0, max_value=1.0)

    @classmethod
    def from_dict(cls, data: Any) -> "Point":
        mapping = require_keys(
            data,
            allowed={"x", "y"},
            required={"x", "y"},
            field="point",
        )
        return cls(
            x=require_number(mapping["x"], "point.x", min_value=0.0, max_value=1.0),
            y=require_number(mapping["y"], "point.y", min_value=0.0, max_value=1.0),
        )


@dataclass(frozen=True, slots=True)
class DrawingAction(ContractModel):
    """One ordered drawing step aligned to the narration.

    ``segment_id`` is the synchronization anchor. Absolute timestamps are
    intentionally absent because narration audio timing is resolved downstream.
    """

    action_id: str
    action_type: DrawingActionType
    segment_id: str
    region_id: str
    order: int
    text: str | None = None
    points: tuple[Point, ...] = ()
    color: str | None = None
    stroke_width: float | None = None
    duration_seconds: float | None = None
    label: str | None = None

    def __post_init__(self) -> None:
        require_id(self.action_id, "action.action_id")
        if not isinstance(self.action_type, DrawingActionType):
            object.__setattr__(
                self,
                "action_type",
                require_enum(self.action_type, DrawingActionType, "action.action_type"),
            )
        require_id(self.segment_id, "action.segment_id")
        require_id(self.region_id, "action.region_id")
        require_int(self.order, "action.order", min_value=1)

        points = ensure_sequence(self.points, "action.points")
        if not all(isinstance(point, Point) for point in points):
            raise ContractError("action.points must contain Point objects")
        object.__setattr__(self, "points", tuple(points))
        if len(self.points) > MAX_ACTION_POINTS:
            raise ContractError(
                f"action.points must contain at most {MAX_ACTION_POINTS} points"
            )

        if self.action_type.value in _WRITE_ACTION_TYPES:
            require_text(self.text, "action.text", max_length=MAX_WRITE_TEXT_LENGTH)
            if self.points:
                raise ContractError("write actions must not define points")
        else:
            optional_text(self.text, "action.text", max_length=MAX_NON_WRITE_TEXT_LENGTH)
            if self.action_type in {
                DrawingActionType.DRAW_LINE,
                DrawingActionType.DRAW_ARROW,
                DrawingActionType.DRAW_RECTANGLE,
                DrawingActionType.DRAW_CIRCLE,
            } and len(self.points) != 2:
                raise ContractError(
                    f"action '{self.action_id}' requires exactly 2 points for "
                    f"{self.action_type.value}"
                )
            if self.action_type in {
                DrawingActionType.DRAW_PATH,
                DrawingActionType.ERASE,
            } and len(self.points) < 2:
                raise ContractError(
                    f"action '{self.action_id}' requires at least 2 points for "
                    f"{self.action_type.value}"
                )

        optional_text(self.color, "action.color", max_length=32)
        optional_number(
            self.stroke_width, "action.stroke_width", min_value=0.5, max_value=20.0
        )
        optional_number(
            self.duration_seconds,
            "action.duration_seconds",
            min_value=0.1,
            max_value=600.0,
        )
        optional_text(self.label, "action.label", max_length=200)

    @classmethod
    def from_dict(cls, data: Any) -> "DrawingAction":
        mapping = require_keys(
            data,
            allowed={
                "action_id",
                "action_type",
                "segment_id",
                "region_id",
                "order",
                "text",
                "points",
                "color",
                "stroke_width",
                "duration_seconds",
                "label",
            },
            required={
                "action_id",
                "action_type",
                "segment_id",
                "region_id",
                "order",
            },
            field="action",
        )
        point_items = ensure_sequence(mapping.get("points", []), "action.points")
        action_type = require_enum(
            mapping["action_type"], DrawingActionType, "action.action_type"
        )
        text_max_length = (
            MAX_WRITE_TEXT_LENGTH
            if action_type.value in _WRITE_ACTION_TYPES
            else MAX_NON_WRITE_TEXT_LENGTH
        )
        return cls(
            action_id=require_id(mapping["action_id"], "action.action_id"),
            action_type=action_type,
            segment_id=require_id(mapping["segment_id"], "action.segment_id"),
            region_id=require_id(mapping["region_id"], "action.region_id"),
            order=require_int(mapping["order"], "action.order", min_value=1),
            text=optional_text(
                mapping.get("text"),
                "action.text",
                max_length=text_max_length,
            ),
            points=tuple(Point.from_dict(item) for item in point_items),
            color=optional_text(mapping.get("color"), "action.color", max_length=32),
            stroke_width=optional_number(
                mapping.get("stroke_width"),
                "action.stroke_width",
                min_value=0.5,
                max_value=20.0,
            ),
            duration_seconds=optional_number(
                mapping.get("duration_seconds"),
                "action.duration_seconds",
                min_value=0.1,
                max_value=600.0,
            ),
            label=optional_text(mapping.get("label"), "action.label", max_length=200),
        )


@dataclass(frozen=True, slots=True)
class DrawingPlan(ContractModel):
    """Ordered set of drawing actions for one scene."""

    actions: tuple[DrawingAction, ...]

    def __post_init__(self) -> None:
        actions = ensure_sequence(self.actions, "drawing.actions")
        if not actions:
            raise ContractError("drawing.actions must contain at least one action")
        if len(actions) > MAX_DRAWING_ACTIONS:
            raise ContractError(
                f"drawing.actions must contain at most {MAX_DRAWING_ACTIONS} actions"
            )
        if not all(isinstance(action, DrawingAction) for action in actions):
            raise ContractError("drawing.actions must contain DrawingAction objects")
        object.__setattr__(self, "actions", tuple(actions))
        require_unique(
            [action.action_id for action in self.actions],
            "drawing.actions.action_id",
        )
        require_unique(
            [action.order for action in self.actions], "drawing.actions.order"
        )
        require_strictly_increasing(
            [action.order for action in self.actions], "drawing.actions.order"
        )

    @property
    def action_ids(self) -> tuple[str, ...]:
        return tuple(action.action_id for action in self.actions)

    @property
    def write_action_ids(self) -> tuple[str, ...]:
        return tuple(
            action.action_id
            for action in self.actions
            if action.action_type.value in _WRITE_ACTION_TYPES
        )

    def action_by_id(self, action_id: str) -> DrawingAction:
        for action in self.actions:
            if action.action_id == action_id:
                return action
        raise ContractError(f"drawing plan does not contain action_id '{action_id}'")

    @classmethod
    def from_dict(cls, data: Any) -> "DrawingPlan":
        mapping = require_keys(
            data,
            allowed={"actions"},
            required={"actions"},
            field="drawing",
        )
        action_items = ensure_sequence(mapping["actions"], "drawing.actions")
        return cls(
            actions=tuple(DrawingAction.from_dict(item) for item in action_items)
        )


__all__ = [
    "DrawingAction",
    "DrawingActionType",
    "DrawingPlan",
    "MAX_ACTION_POINTS",
    "MAX_DRAWING_ACTIONS",
    "MAX_NON_WRITE_TEXT_LENGTH",
    "MAX_WRITE_TEXT_LENGTH",
    "Point",
]
