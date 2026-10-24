"""Scene script and scene-planning input contracts.

Responsibility: define the structured output of the Script Agent and the shared
scene scope used by both scene-planning agents.
Workflow: planning provides ``ScenePlanningInput``; the Script Agent produces a
``SceneScript``; the Whiteboard Director Agent consumes both.
Inputs: scene briefs, selected pages, source excerpts, personalization guidance,
and validated LLM output dictionaries.
Outputs: immutable ``SceneScript``, ``NarrationSegment``, ``KeyPoint``, and
``ScenePlanningInput`` objects.
Interacts with: ``models.lesson_plan``, ``models.source``, ``models.scene_planning``,
whiteboard contracts, prompts, validation, and downstream packaging.
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
    require_number,
    require_text,
    require_text_tuple,
    require_unique,
)
from .lesson_plan import LessonPlan, PersonalizationBrief, SceneBrief
from .source import (
    PageRange,
    SourceExcerpt,
    require_source_pages_selected,
    require_unique_source_ids,
)

SCENE_SCRIPT_SCHEMA_VERSION = "1.0"

SUPPORTED_SCENE_SCRIPT_SCHEMA_VERSIONS = frozenset({SCENE_SCRIPT_SCHEMA_VERSION})

# Public limits shared with the JSON Schema so runtime and provider-facing
# validation cannot drift apart.
MAX_SCENE_SEGMENTS = 40
MAX_SCENE_KEY_POINTS = 30
MAX_SEGMENT_KEY_POINTS = 20
MAX_KEY_POINT_SOURCE_IDS = 20
MAX_NARRATION_SEGMENT_TEXT_LENGTH = 4_000
MAX_KEY_POINT_TEXT_LENGTH = 2_000


class KeyPointImportance(str, Enum):
    """Relative importance of a teaching point within a scene."""

    CORE = "core"
    SUPPORTING = "supporting"


@dataclass(frozen=True, slots=True)
class NarrationSegment(ContractModel):
    """One ordered unit of narration, used as the timing anchor for drawing cues."""

    segment_id: str
    text: str
    estimated_duration_seconds: float
    key_point_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        require_id(self.segment_id, "segment.segment_id")
        require_text(
            self.text,
            "segment.text",
            max_length=MAX_NARRATION_SEGMENT_TEXT_LENGTH,
        )
        require_number(
            self.estimated_duration_seconds,
            "segment.estimated_duration_seconds",
            min_value=0.5,
            max_value=600.0,
        )
        point_ids = require_id_tuple(
            self.key_point_ids,
            "segment.key_point_ids",
            min_items=1,
            max_items=MAX_SEGMENT_KEY_POINTS,
        )
        require_unique(point_ids, "segment.key_point_ids")
        object.__setattr__(self, "key_point_ids", point_ids)

    @classmethod
    def from_dict(cls, data: Any) -> "NarrationSegment":
        mapping = require_keys(
            data,
            allowed={
                "segment_id",
                "text",
                "estimated_duration_seconds",
                "key_point_ids",
            },
            required={
                "segment_id",
                "text",
                "estimated_duration_seconds",
                "key_point_ids",
            },
            field="segment",
        )
        return cls(
            segment_id=require_id(mapping["segment_id"], "segment.segment_id"),
            text=require_text(
                mapping["text"],
                "segment.text",
                max_length=MAX_NARRATION_SEGMENT_TEXT_LENGTH,
            ),
            estimated_duration_seconds=require_number(
                mapping["estimated_duration_seconds"],
                "segment.estimated_duration_seconds",
                min_value=0.5,
                max_value=600.0,
            ),
            key_point_ids=require_id_tuple(
                mapping["key_point_ids"],
                "segment.key_point_ids",
                min_items=1,
                max_items=MAX_SEGMENT_KEY_POINTS,
            ),
        )


@dataclass(frozen=True, slots=True)
class KeyPoint(ContractModel):
    """A teaching point grounded in one or more upstream source excerpts."""

    key_point_id: str
    text: str
    source_ids: tuple[str, ...]
    importance: KeyPointImportance = KeyPointImportance.CORE

    def __post_init__(self) -> None:
        require_id(self.key_point_id, "key_point.key_point_id")
        require_text(self.text, "key_point.text", max_length=MAX_KEY_POINT_TEXT_LENGTH)
        source_ids = require_id_tuple(
            self.source_ids,
            "key_point.source_ids",
            min_items=1,
            max_items=MAX_KEY_POINT_SOURCE_IDS,
        )
        object.__setattr__(self, "source_ids", source_ids)
        require_unique(self.source_ids, "key_point.source_ids")
        if not isinstance(self.importance, KeyPointImportance):
            object.__setattr__(
                self,
                "importance",
                require_enum(
                    self.importance, KeyPointImportance, "key_point.importance"
                ),
            )

    @classmethod
    def from_dict(cls, data: Any) -> "KeyPoint":
        mapping = require_keys(
            data,
            allowed={"key_point_id", "text", "source_ids", "importance"},
            required={"key_point_id", "text", "source_ids"},
            field="key_point",
        )
        return cls(
            key_point_id=require_id(
                mapping["key_point_id"], "key_point.key_point_id"
            ),
            text=require_text(
                mapping["text"],
                "key_point.text",
                max_length=MAX_KEY_POINT_TEXT_LENGTH,
            ),
            source_ids=require_id_tuple(
                mapping["source_ids"],
                "key_point.source_ids",
                min_items=1,
                max_items=MAX_KEY_POINT_SOURCE_IDS,
            ),
            importance=require_enum(
                mapping.get("importance", KeyPointImportance.CORE.value),
                KeyPointImportance,
                "key_point.importance",
            ),
        )


@dataclass(frozen=True, slots=True)
class SceneScript(ContractModel):
    """Validated narration and key points for one scene.

    ``estimated_duration_seconds`` and ``narration`` are derived properties so
    the per-segment timing remains the single source of truth. Whiteboard cues
    anchor to ``segment_id`` values rather than absolute timestamps.
    """

    scene_id: str
    language: str
    segments: tuple[NarrationSegment, ...]
    key_points: tuple[KeyPoint, ...]
    delivery_notes: tuple[str, ...] = ()
    schema_version: str = SCENE_SCRIPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version not in SUPPORTED_SCENE_SCRIPT_SCHEMA_VERSIONS:
            raise ContractError(
                f"script.schema_version must be one of: "
                f"{', '.join(sorted(SUPPORTED_SCENE_SCRIPT_SCHEMA_VERSIONS))}"
            )
        require_id(self.scene_id, "script.scene_id")
        require_text(self.language, "script.language", max_length=64)

        segments = ensure_sequence(self.segments, "script.segments")
        if not segments:
            raise ContractError("script.segments must contain at least one segment")
        if len(segments) > MAX_SCENE_SEGMENTS:
            raise ContractError(
                f"script.segments must contain at most {MAX_SCENE_SEGMENTS} segments"
            )
        if not all(isinstance(segment, NarrationSegment) for segment in segments):
            raise ContractError("script.segments must contain NarrationSegment objects")
        object.__setattr__(self, "segments", tuple(segments))
        require_unique(
            [segment.segment_id for segment in self.segments],
            "script.segments.segment_id",
        )

        key_points = ensure_sequence(self.key_points, "script.key_points")
        if not key_points:
            raise ContractError("script.key_points must contain at least one key point")
        if len(key_points) > MAX_SCENE_KEY_POINTS:
            raise ContractError(
                "script.key_points must contain at most "
                f"{MAX_SCENE_KEY_POINTS} key points"
            )
        if not all(isinstance(point, KeyPoint) for point in key_points):
            raise ContractError("script.key_points must contain KeyPoint objects")
        object.__setattr__(self, "key_points", tuple(key_points))
        require_unique(
            [point.key_point_id for point in self.key_points],
            "script.key_points.key_point_id",
        )

        notes = require_text_tuple(
            self.delivery_notes,
            "script.delivery_notes",
            max_items=20,
            max_length=1_000,
        )
        object.__setattr__(self, "delivery_notes", notes)

        known_points = {point.key_point_id for point in self.key_points}
        referenced_points: set[str] = set()
        for segment in self.segments:
            missing = set(segment.key_point_ids) - known_points
            if missing:
                raise ContractError(
                    f"script segment '{segment.segment_id}' references unknown "
                    f"key_point_id(s): {', '.join(sorted(missing))}"
                )
            referenced_points.update(segment.key_point_ids)
        unreferenced = known_points - referenced_points
        if unreferenced:
            raise ContractError(
                "script key_point_id(s) are not used by any segment: "
                + ", ".join(sorted(unreferenced))
            )

    @property
    def estimated_duration_seconds(self) -> float:
        return round(
            sum(segment.estimated_duration_seconds for segment in self.segments), 3
        )

    @property
    def narration(self) -> str:
        return " ".join(segment.text for segment in self.segments)

    @property
    def word_count(self) -> int:
        return len(self.narration.split())

    @property
    def cited_source_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    source_id
                    for point in self.key_points
                    for source_id in point.source_ids
                }
            )
        )

    def to_prompt_dict(self) -> dict[str, Any]:
        """Return script context for a whiteboard prompt or downstream review."""
        data = self.to_dict()
        data["narration"] = self.narration
        data["estimated_duration_seconds"] = self.estimated_duration_seconds
        return data

    @classmethod
    def from_dict(cls, data: Any) -> "SceneScript":
        mapping = require_keys(
            data,
            allowed={
                "scene_id",
                "language",
                "segments",
                "key_points",
                "delivery_notes",
                "schema_version",
            },
            required={"scene_id", "language", "segments", "key_points"},
            field="script",
        )
        segment_items = ensure_sequence(mapping["segments"], "script.segments")
        point_items = ensure_sequence(mapping["key_points"], "script.key_points")
        notes = mapping.get("delivery_notes", [])
        return cls(
            scene_id=require_id(mapping["scene_id"], "script.scene_id"),
            language=require_text(
                mapping["language"], "script.language", max_length=64
            ),
            segments=tuple(
                NarrationSegment.from_dict(item) for item in segment_items
            ),
            key_points=tuple(KeyPoint.from_dict(item) for item in point_items),
            delivery_notes=require_text_tuple(
                notes,
                "script.delivery_notes",
                max_items=20,
                max_length=1_000,
            ),
            schema_version=require_text(
                mapping.get("schema_version", SCENE_SCRIPT_SCHEMA_VERSION),
                "script.schema_version",
                max_length=32,
            ),
        )


@dataclass(frozen=True, slots=True)
class ScenePlanningInput(ContractModel):
    """Shared input for the Script Agent and Whiteboard Director Agent."""

    lesson_id: str
    scene: SceneBrief
    preferences: PersonalizationBrief
    selected_pages: tuple[PageRange, ...]

    def __post_init__(self) -> None:
        require_id(self.lesson_id, "input.lesson_id")
        if not isinstance(self.scene, SceneBrief):
            raise ContractError("input.scene must be a SceneBrief")
        if not isinstance(self.preferences, PersonalizationBrief):
            raise ContractError("input.preferences must be a PersonalizationBrief")

        pages = ensure_sequence(self.selected_pages, "input.selected_pages")
        if not pages:
            raise ContractError("input.selected_pages must contain at least one range")
        if not all(isinstance(page_range, PageRange) for page_range in pages):
            raise ContractError("input.selected_pages must contain PageRange objects")
        object.__setattr__(self, "selected_pages", tuple(pages))
        require_unique(
            [(item.start_page, item.end_page) for item in self.selected_pages],
            "input.selected_pages",
        )
        require_unique_source_ids(
            self.scene.source_excerpts, "input.scene.source_excerpts"
        )
        require_source_pages_selected(
            self.scene.source_excerpts,
            self.selected_pages,
            "input.scene",
        )

    @property
    def source_excerpts(self) -> tuple[SourceExcerpt, ...]:
        return self.scene.source_excerpts

    @property
    def source_by_id(self) -> dict[str, SourceExcerpt]:
        return {
            excerpt.source_id: excerpt for excerpt in self.scene.source_excerpts
        }

    @property
    def allowed_source_ids(self) -> tuple[str, ...]:
        return tuple(excerpt.source_id for excerpt in self.scene.source_excerpts)

    @property
    def target_duration_seconds(self) -> float:
        return self.scene.target_duration_seconds

    @classmethod
    def from_lesson_plan(
        cls, lesson_plan: LessonPlan, scene_id: str
    ) -> "ScenePlanningInput":
        if not isinstance(lesson_plan, LessonPlan):
            raise ContractError("lesson_plan must be a LessonPlan")
        return cls(
            lesson_id=lesson_plan.lesson_id,
            scene=lesson_plan.scene_by_id(scene_id),
            preferences=lesson_plan.preferences,
            selected_pages=lesson_plan.selected_pages,
        )

    @classmethod
    def from_dict(cls, data: Any) -> "ScenePlanningInput":
        mapping = require_keys(
            data,
            allowed={"lesson_id", "scene", "preferences", "selected_pages"},
            required={"lesson_id", "scene", "preferences", "selected_pages"},
            field="input",
        )
        pages_data = ensure_sequence(mapping["selected_pages"], "input.selected_pages")
        return cls(
            lesson_id=require_id(mapping["lesson_id"], "input.lesson_id"),
            scene=SceneBrief.from_dict(mapping["scene"]),
            preferences=PersonalizationBrief.from_dict(mapping["preferences"]),
            selected_pages=tuple(PageRange.from_dict(item) for item in pages_data),
        )


__all__ = [
    "KeyPoint",
    "KeyPointImportance",
    "MAX_KEY_POINT_SOURCE_IDS",
    "MAX_KEY_POINT_TEXT_LENGTH",
    "MAX_NARRATION_SEGMENT_TEXT_LENGTH",
    "MAX_SCENE_KEY_POINTS",
    "MAX_SCENE_SEGMENTS",
    "MAX_SEGMENT_KEY_POINTS",
    "NarrationSegment",
    "SCENE_SCRIPT_SCHEMA_VERSION",
    "ScenePlanningInput",
    "SceneScript",
]
