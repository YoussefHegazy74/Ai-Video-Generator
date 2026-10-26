"""Provider-neutral visual-plan contracts for one scene.

Responsibility: describe the teaching visuals, examples, analogies, diagrams,
and illustrations a scene needs, together with stable references to the script,
key points, and source excerpts that justify them.
Workflow: produced by the future Example & Visual Agent and consumed by the
Scene Packager alongside the script, whiteboard plan, timing, and source context.
Inputs: stable visual identifiers, visual purpose and kind, learner-facing
descriptions, and references to existing scene contracts.
Outputs: immutable ``VisualPlan`` and ``VisualItem`` objects with stable JSON
serialization.
Interacts with: ``models.scene``, ``models.source``, ``models.lesson_plan``, the
whiteboard contracts, and downstream packaging.

Ownership boundaries: this module describes *what* should be shown, not *how* to
draw or render it. It contains no board coordinates, drawing primitives,
timestamps, provider prompts, generated asset URLs, or rendering instructions.
The Whiteboard Director owns layout and spatial execution; the Scene Packager
owns provider-neutral composition; the renderer owns execution.

External asset storage and retrieval do not exist in the current architecture.
Visual items therefore describe intent and required on-screen text only; they do
not carry asset identifiers or URLs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .base import (
    ContractError,
    ContractModel,
    ensure_sequence,
    require_enum,
    require_id,
    require_id_tuple,
    require_keys,
    require_text,
    require_text_tuple,
    require_unique,
)

VISUAL_PLAN_SCHEMA_VERSION = "1.0"

SUPPORTED_VISUAL_PLAN_SCHEMA_VERSIONS = frozenset({VISUAL_PLAN_SCHEMA_VERSION})

MAX_VISUAL_DESCRIPTION_LENGTH = 2_000
MAX_VISUAL_EXACT_TEXT_LENGTH = 2_000
MAX_VISUAL_ACCESSIBILITY_NOTES = 20
MAX_VISUAL_ACCESSIBILITY_NOTE_LENGTH = 1_000


class VisualPurpose(str, Enum):
    """Pedagogical reason for showing a visual."""

    EXPLAIN_CONCEPT = "explain_concept"
    ILLUSTRATE_EXAMPLE = "illustrate_example"
    SHOW_PROCESS = "show_process"
    COMPARE_ALTERNATIVES = "compare_alternatives"
    SUMMARIZE_KEY_IDEA = "summarize_key_idea"


class VisualKind(str, Enum):
    """Category of teaching visual requested for a scene."""

    DIAGRAM = "diagram"
    REAL_WORLD_EXAMPLE = "real_world_example"
    ANALOGY = "analogy"
    ILLUSTRATION = "illustration"
    CHART = "chart"
    OTHER = "other"


def _unique_id_tuple(value: Any, field: str) -> tuple[str, ...]:
    """Validate an optional identifier sequence without normalizing it."""
    identifiers = require_id_tuple(value, field)
    require_unique(identifiers, field)
    return identifiers


@dataclass(frozen=True, slots=True)
class VisualItem(ContractModel):
    """One ordered teaching-visual request within a scene.

    Only ``visual_id``, ``purpose``, ``kind``, and ``description`` are required.
    References to narration segments, key points, and source excerpts are
    optional because a visual may span the whole scene or be self-explanatory.
    ``exact_text`` preserves labels, formulas, and code exactly as supplied; the
    contract never normalizes that text.
    """

    visual_id: str
    purpose: VisualPurpose
    kind: VisualKind
    description: str
    segment_ids: tuple[str, ...] = ()
    key_point_ids: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    exact_text: tuple[str, ...] = ()
    accessibility_notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_id(self.visual_id, "visual_item.visual_id")
        if not isinstance(self.purpose, VisualPurpose):
            object.__setattr__(
                self,
                "purpose",
                require_enum(
                    self.purpose, VisualPurpose, "visual_item.purpose"
                ),
            )
        if not isinstance(self.kind, VisualKind):
            object.__setattr__(
                self,
                "kind",
                require_enum(self.kind, VisualKind, "visual_item.kind"),
            )
        require_text(
            self.description,
            "visual_item.description",
            max_length=MAX_VISUAL_DESCRIPTION_LENGTH,
        )
        object.__setattr__(
            self,
            "segment_ids",
            _unique_id_tuple(self.segment_ids, "visual_item.segment_ids"),
        )
        object.__setattr__(
            self,
            "key_point_ids",
            _unique_id_tuple(self.key_point_ids, "visual_item.key_point_ids"),
        )
        object.__setattr__(
            self,
            "source_ids",
            _unique_id_tuple(self.source_ids, "visual_item.source_ids"),
        )
        object.__setattr__(
            self,
            "exact_text",
            require_text_tuple(
                self.exact_text,
                "visual_item.exact_text",
                max_length=MAX_VISUAL_EXACT_TEXT_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "accessibility_notes",
            require_text_tuple(
                self.accessibility_notes,
                "visual_item.accessibility_notes",
                max_items=MAX_VISUAL_ACCESSIBILITY_NOTES,
                max_length=MAX_VISUAL_ACCESSIBILITY_NOTE_LENGTH,
            ),
        )

    @classmethod
    def from_dict(cls, data: Any) -> "VisualItem":
        mapping = require_keys(
            data,
            allowed={
                "visual_id",
                "purpose",
                "kind",
                "description",
                "segment_ids",
                "key_point_ids",
                "source_ids",
                "exact_text",
                "accessibility_notes",
            },
            required={"visual_id", "purpose", "kind", "description"},
            field="visual_item",
        )
        return cls(
            visual_id=require_id(
                mapping["visual_id"], "visual_item.visual_id"
            ),
            purpose=require_enum(
                mapping["purpose"], VisualPurpose, "visual_item.purpose"
            ),
            kind=require_enum(
                mapping["kind"], VisualKind, "visual_item.kind"
            ),
            description=require_text(
                mapping["description"],
                "visual_item.description",
                max_length=MAX_VISUAL_DESCRIPTION_LENGTH,
            ),
            segment_ids=_unique_id_tuple(
                mapping.get("segment_ids", []), "visual_item.segment_ids"
            ),
            key_point_ids=_unique_id_tuple(
                mapping.get("key_point_ids", []),
                "visual_item.key_point_ids",
            ),
            source_ids=_unique_id_tuple(
                mapping.get("source_ids", []), "visual_item.source_ids"
            ),
            exact_text=require_text_tuple(
                mapping.get("exact_text", []),
                "visual_item.exact_text",
                max_length=MAX_VISUAL_EXACT_TEXT_LENGTH,
            ),
            accessibility_notes=require_text_tuple(
                mapping.get("accessibility_notes", []),
                "visual_item.accessibility_notes",
                max_items=MAX_VISUAL_ACCESSIBILITY_NOTES,
                max_length=MAX_VISUAL_ACCESSIBILITY_NOTE_LENGTH,
            ),
        )


@dataclass(frozen=True, slots=True)
class VisualPlan(ContractModel):
    """Ordered teaching-visual plan for one scene.

    ``items`` may be empty when narration and the whiteboard already carry the
    teaching visuals; this mirrors the optional highlight and handwriting plans.
    ``scene_id`` is owned by upstream planning and must match the scene the plan
    describes.
    """

    scene_id: str
    items: tuple[VisualItem, ...]
    schema_version: str = VISUAL_PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version not in SUPPORTED_VISUAL_PLAN_SCHEMA_VERSIONS:
            raise ContractError(
                "visual_plan.schema_version must be one of: "
                + ", ".join(sorted(SUPPORTED_VISUAL_PLAN_SCHEMA_VERSIONS))
            )
        require_id(self.scene_id, "visual_plan.scene_id")

        items = ensure_sequence(self.items, "visual_plan.items")
        if not all(isinstance(item, VisualItem) for item in items):
            raise ContractError(
                "visual_plan.items must contain VisualItem objects"
            )
        object.__setattr__(self, "items", tuple(items))
        require_unique(
            [item.visual_id for item in self.items],
            "visual_plan.items.visual_id",
        )

    @classmethod
    def from_dict(cls, data: Any) -> "VisualPlan":
        mapping = require_keys(
            data,
            allowed={"scene_id", "items", "schema_version"},
            required={"scene_id", "items"},
            field="visual_plan",
        )
        item_data = ensure_sequence(mapping["items"], "visual_plan.items")
        return cls(
            scene_id=require_id(mapping["scene_id"], "visual_plan.scene_id"),
            items=tuple(VisualItem.from_dict(item) for item in item_data),
            schema_version=require_text(
                mapping.get("schema_version", VISUAL_PLAN_SCHEMA_VERSION),
                "visual_plan.schema_version",
                max_length=32,
            ),
        )


__all__ = [
    "MAX_VISUAL_ACCESSIBILITY_NOTES",
    "MAX_VISUAL_ACCESSIBILITY_NOTE_LENGTH",
    "MAX_VISUAL_DESCRIPTION_LENGTH",
    "MAX_VISUAL_EXACT_TEXT_LENGTH",
    "SUPPORTED_VISUAL_PLAN_SCHEMA_VERSIONS",
    "VISUAL_PLAN_SCHEMA_VERSION",
    "VisualItem",
    "VisualKind",
    "VisualPlan",
    "VisualPurpose",
]
