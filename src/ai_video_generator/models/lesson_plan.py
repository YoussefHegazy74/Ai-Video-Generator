"""Lesson-planning contracts consumed by the scene-planning agents.

Responsibility: define the minimal upstream hand-off from planning and
personalization into the Script Agent and Whiteboard Director Agent.
Workflow: planning produces a ``LessonPlan``; each ``SceneBrief`` becomes the
scope of one Script Agent and Whiteboard Director Agent run.
Inputs: selected pages, personalization guidance, ordered scene briefs, and
source excerpts.
Outputs: validated ``LessonPlan``, ``SceneBrief``, and ``PersonalizationBrief``
objects with stable serialization.
Interacts with: ``models.source``, ``models.scene``, prompt builders, and the
planning/personalization components. This is a provisional adapter contract and
is documented in ``docs/team_component_contracts.md``.
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
    require_int,
    require_keys,
    require_number,
    require_strictly_increasing,
    require_text,
    require_text_tuple,
    require_unique,
)
from .source import (
    PageRange,
    SourceExcerpt,
    require_source_pages_selected,
    require_unique_source_ids,
    source_excerpts_from_sequence,
)


class AudienceLevel(str, Enum):
    """Supported learner sophistication levels."""

    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


@dataclass(frozen=True, slots=True)
class PersonalizationBrief(ContractModel):
    """Personalization guidance passed unchanged to both scene-planning agents."""

    audience_level: AudienceLevel
    language: str
    teaching_style: str
    style_guidance: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.audience_level, AudienceLevel):
            object.__setattr__(
                self,
                "audience_level",
                require_enum(
                    self.audience_level, AudienceLevel, "preferences.audience_level"
                ),
            )
        require_text(self.language, "preferences.language", max_length=64)
        require_text(self.teaching_style, "preferences.teaching_style", max_length=120)
        require_text(
            self.style_guidance,
            "preferences.style_guidance",
            max_length=4_000,
            allow_empty=True,
        )

    @classmethod
    def from_dict(cls, data: Any) -> "PersonalizationBrief":
        mapping = require_keys(
            data,
            allowed={"audience_level", "language", "teaching_style", "style_guidance"},
            required={"audience_level", "language", "teaching_style"},
            field="preferences",
        )
        return cls(
            audience_level=require_enum(
                mapping["audience_level"],
                AudienceLevel,
                "preferences.audience_level",
            ),
            language=require_text(
                mapping["language"], "preferences.language", max_length=64
            ),
            teaching_style=require_text(
                mapping["teaching_style"], "preferences.teaching_style", max_length=120
            ),
            style_guidance=require_text(
                mapping.get("style_guidance", ""),
                "preferences.style_guidance",
                max_length=4_000,
                allow_empty=True,
            ),
        )


@dataclass(frozen=True, slots=True)
class SceneBrief(ContractModel):
    """One planned scene supplied by the upstream planning component.

    ``scene_id`` and ``sequence`` are owned by planning and must remain stable.
    The two scene-planning agents may add content but must never renumber or
    rename the scene.
    """

    scene_id: str
    sequence: int
    title: str
    objective: str
    topics: tuple[str, ...]
    target_duration_seconds: float
    source_excerpts: tuple[SourceExcerpt, ...]

    def __post_init__(self) -> None:
        require_id(self.scene_id, "scene.scene_id")
        require_int(self.sequence, "scene.sequence", min_value=1)
        require_text(self.title, "scene.title", max_length=300)
        require_text(self.objective, "scene.objective", max_length=4_000)
        topics = require_text_tuple(
            self.topics, "scene.topics", min_items=1, max_items=30, max_length=300
        )
        object.__setattr__(self, "topics", topics)
        require_number(
            self.target_duration_seconds,
            "scene.target_duration_seconds",
            min_value=1.0,
            max_value=3_600.0,
        )
        excerpts = ensure_sequence(self.source_excerpts, "scene.source_excerpts")
        if not excerpts:
            raise ContractError("scene.source_excerpts must contain at least one excerpt")
        if not all(isinstance(excerpt, SourceExcerpt) for excerpt in excerpts):
            raise ContractError("scene.source_excerpts must contain SourceExcerpt objects")
        object.__setattr__(self, "source_excerpts", tuple(excerpts))
        require_unique_source_ids(self.source_excerpts, "scene.source_excerpts")

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(excerpt.source_id for excerpt in self.source_excerpts)

    @classmethod
    def from_dict(cls, data: Any) -> "SceneBrief":
        mapping = require_keys(
            data,
            allowed={
                "scene_id",
                "sequence",
                "title",
                "objective",
                "topics",
                "target_duration_seconds",
                "source_excerpts",
            },
            required={
                "scene_id",
                "sequence",
                "title",
                "objective",
                "topics",
                "target_duration_seconds",
                "source_excerpts",
            },
            field="scene",
        )
        return cls(
            scene_id=require_id(mapping["scene_id"], "scene.scene_id"),
            sequence=require_int(mapping["sequence"], "scene.sequence", min_value=1),
            title=require_text(mapping["title"], "scene.title", max_length=300),
            objective=require_text(
                mapping["objective"], "scene.objective", max_length=4_000
            ),
            topics=require_text_tuple(
                mapping["topics"],
                "scene.topics",
                min_items=1,
                max_items=30,
                max_length=300,
            ),
            target_duration_seconds=require_number(
                mapping["target_duration_seconds"],
                "scene.target_duration_seconds",
                min_value=1.0,
                max_value=3_600.0,
            ),
            source_excerpts=source_excerpts_from_sequence(
                mapping["source_excerpts"], "scene.source_excerpts"
            ),
        )


@dataclass(frozen=True, slots=True)
class LessonPlan(ContractModel):
    """Ordered lesson plan and source selection for one project.

    The model is intentionally small: it is the contract the planning component
    must provide, not an implementation of the planning workflow.
    """

    lesson_id: str
    title: str
    selected_pages: tuple[PageRange, ...]
    preferences: PersonalizationBrief
    scenes: tuple[SceneBrief, ...]

    def __post_init__(self) -> None:
        require_id(self.lesson_id, "lesson.lesson_id")
        require_text(self.title, "lesson.title", max_length=500)
        if not isinstance(self.preferences, PersonalizationBrief):
            raise ContractError("lesson.preferences must be a PersonalizationBrief")
        pages = ensure_sequence(self.selected_pages, "lesson.selected_pages")
        if not pages:
            raise ContractError("lesson.selected_pages must contain at least one range")
        if not all(isinstance(page_range, PageRange) for page_range in pages):
            raise ContractError("lesson.selected_pages must contain PageRange objects")
        object.__setattr__(self, "selected_pages", tuple(pages))
        require_unique(
            [(item.start_page, item.end_page) for item in self.selected_pages],
            "lesson.selected_pages",
        )

        scenes = ensure_sequence(self.scenes, "lesson.scenes")
        if not scenes:
            raise ContractError("lesson.scenes must contain at least one scene")
        if not all(isinstance(scene, SceneBrief) for scene in scenes):
            raise ContractError("lesson.scenes must contain SceneBrief objects")
        object.__setattr__(self, "scenes", tuple(scenes))
        require_unique([scene.scene_id for scene in self.scenes], "lesson.scenes.scene_id")
        require_strictly_increasing(
            [scene.sequence for scene in self.scenes], "lesson.scenes.sequence"
        )
        for scene in self.scenes:
            require_source_pages_selected(
                scene.source_excerpts,
                self.selected_pages,
                f"lesson scene '{scene.scene_id}'",
            )

    @property
    def total_target_duration_seconds(self) -> float:
        return sum(scene.target_duration_seconds for scene in self.scenes)

    def scene_by_id(self, scene_id: str) -> SceneBrief:
        for scene in self.scenes:
            if scene.scene_id == scene_id:
                return scene
        raise ContractError(f"lesson does not contain scene_id '{scene_id}'")

    @classmethod
    def from_dict(cls, data: Any) -> "LessonPlan":
        mapping = require_keys(
            data,
            allowed={"lesson_id", "title", "selected_pages", "preferences", "scenes"},
            required={"lesson_id", "title", "selected_pages", "preferences", "scenes"},
            field="lesson",
        )
        pages_data = ensure_sequence(mapping["selected_pages"], "lesson.selected_pages")
        scenes_data = ensure_sequence(mapping["scenes"], "lesson.scenes")
        return cls(
            lesson_id=require_id(mapping["lesson_id"], "lesson.lesson_id"),
            title=require_text(mapping["title"], "lesson.title", max_length=500),
            selected_pages=tuple(PageRange.from_dict(item) for item in pages_data),
            preferences=PersonalizationBrief.from_dict(mapping["preferences"]),
            scenes=tuple(SceneBrief.from_dict(item) for item in scenes_data),
        )


__all__ = [
    "AudienceLevel",
    "LessonPlan",
    "PersonalizationBrief",
    "SceneBrief",
]
