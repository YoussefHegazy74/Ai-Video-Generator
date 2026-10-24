"""Whiteboard visual-continuity contract.

Responsibility: describe how a scene starts relative to the previous scene:
fresh board, continued board, or selective region clearing.
Workflow: produced by the Whiteboard Director Agent for each scene and consumed
by scene packaging, renderers, and continuity validation.
Inputs: continuity mode, carried-over drawing action IDs, cleared region IDs, and
human-readable notes.
Outputs: validated ``ContinuityPlan`` objects.
Interacts with: ``whiteboard.plan``, validation, and the downstream video
continuity checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..models.base import (
    ContractError,
    ContractModel,
    ensure_sequence,
    require_enum,
    require_id_tuple,
    require_keys,
    require_text_tuple,
    require_unique,
)

# Shared with the provider-facing JSON Schema.
MAX_CONTINUITY_IDS = 100
MAX_CONTINUITY_NOTES = 20
MAX_CONTINUITY_NOTE_LENGTH = 500


class ContinuityMode(str, Enum):
    """How a whiteboard scene begins relative to its predecessor."""

    FRESH_BOARD = "fresh_board"
    CONTINUE_BOARD = "continue_board"
    CLEAR_REGIONS = "clear_regions"


@dataclass(frozen=True, slots=True)
class ContinuityPlan(ContractModel):
    """Carry-over and clearing decisions for one scene boundary."""

    mode: ContinuityMode
    carry_over_action_ids: tuple[str, ...] = ()
    clear_region_ids: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.mode, ContinuityMode):
            object.__setattr__(
                self,
                "mode",
                require_enum(self.mode, ContinuityMode, "continuity.mode"),
            )
        carried = require_id_tuple(
            self.carry_over_action_ids,
            "continuity.carry_over_action_ids",
            max_items=MAX_CONTINUITY_IDS,
        )
        cleared = require_id_tuple(
            self.clear_region_ids,
            "continuity.clear_region_ids",
            max_items=MAX_CONTINUITY_IDS,
        )
        notes = require_text_tuple(
            self.notes,
            "continuity.notes",
            max_items=MAX_CONTINUITY_NOTES,
            max_length=MAX_CONTINUITY_NOTE_LENGTH,
        )
        object.__setattr__(self, "carry_over_action_ids", carried)
        object.__setattr__(self, "clear_region_ids", cleared)
        object.__setattr__(self, "notes", notes)
        require_unique(carried, "continuity.carry_over_action_ids")
        require_unique(cleared, "continuity.clear_region_ids")

        if self.mode is ContinuityMode.FRESH_BOARD:
            if carried or cleared:
                raise ContractError(
                    "fresh_board continuity must not carry over actions or clear regions"
                )
        elif self.mode is ContinuityMode.CONTINUE_BOARD:
            if not carried:
                raise ContractError(
                    "continue_board continuity must carry over at least one action"
                )
        elif self.mode is ContinuityMode.CLEAR_REGIONS:
            if not cleared:
                raise ContractError(
                    "clear_regions continuity must specify at least one region to clear"
                )

    @classmethod
    def from_dict(cls, data: Any) -> "ContinuityPlan":
        mapping = require_keys(
            data,
            allowed={
                "mode",
                "carry_over_action_ids",
                "clear_region_ids",
                "notes",
            },
            required={"mode"},
            field="continuity",
        )
        carried = ensure_sequence(
            mapping.get("carry_over_action_ids", []),
            "continuity.carry_over_action_ids",
        )
        cleared = ensure_sequence(
            mapping.get("clear_region_ids", []), "continuity.clear_region_ids"
        )
        notes = ensure_sequence(mapping.get("notes", []), "continuity.notes")
        return cls(
            mode=require_enum(mapping["mode"], ContinuityMode, "continuity.mode"),
            carry_over_action_ids=require_id_tuple(
                carried,
                "continuity.carry_over_action_ids",
                max_items=MAX_CONTINUITY_IDS,
            ),
            clear_region_ids=require_id_tuple(
                cleared,
                "continuity.clear_region_ids",
                max_items=MAX_CONTINUITY_IDS,
            ),
            notes=require_text_tuple(
                notes,
                "continuity.notes",
                max_items=MAX_CONTINUITY_NOTES,
                max_length=MAX_CONTINUITY_NOTE_LENGTH,
            ),
        )


__all__ = [
    "ContinuityMode",
    "ContinuityPlan",
    "MAX_CONTINUITY_IDS",
    "MAX_CONTINUITY_NOTES",
    "MAX_CONTINUITY_NOTE_LENGTH",
]
