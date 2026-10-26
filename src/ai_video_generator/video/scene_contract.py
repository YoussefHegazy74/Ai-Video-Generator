"""Canonical, provider-neutral scene package contract.

Responsibility: compose the validated artifacts that describe one planned scene
so packaging, generation, validation, and assembly share one stable structure.
Workflow: assembled after script, whiteboard, visual, and timing planning; before
provider-specific packaging and media generation.
Inputs: a stable lesson/scene identity, the validated ``SceneScript``,
``VisualPlan``, ``WhiteboardPlan``, ``SceneTimeline``, and source excerpts.
Outputs: an immutable ``ScenePackage`` with strict, deterministic JSON
serialization and deserialization.
Interacts with: ``models.scene``, ``models.visual_plan``, ``models.source``,
``whiteboard.plan``, ``video.scene_timing``, and the future Scene Packager.

The contract is provider-neutral. It contains no provider prompts, model names,
asset URLs, storage details, renderer instructions, or actual audio timestamps.
``SceneTimeline`` is explicitly a planning estimate. The Whiteboard Director
retains ownership of layout and drawing primitives; this contract only ensures
that the plan belongs to the same scene and references existing segments.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ..models.base import (
    ContractError,
    ContractModel,
    ensure_sequence,
    require_id,
    require_keys,
    require_text,
)
from ..models.scene import SceneScript
from ..models.source import (
    SourceExcerpt,
    require_unique_source_ids,
    source_excerpts_from_sequence,
)
from ..models.visual_plan import VisualPlan
from ..whiteboard.plan import WhiteboardPlan
from .scene_timing import SceneTimeline

SCENE_PACKAGE_SCHEMA_VERSION = "1.0"

SUPPORTED_SCENE_PACKAGE_SCHEMA_VERSIONS = frozenset(
    {SCENE_PACKAGE_SCHEMA_VERSION}
)

_TIMING_EPSILON = 1e-6


@dataclass(frozen=True, slots=True)
class ScenePackage(ContractModel):
    """Complete provider-neutral composition for one planned scene.

    Every nested artifact must describe ``scene_id``. The timeline is the
    planning timeline derived from the script's estimated segment durations; it
    is not a source of verified TTS or audio timestamps. Source excerpts are
    preserved in their original order and are used only to validate citations.
    """

    lesson_id: str
    scene_id: str
    script: SceneScript
    visual: VisualPlan
    whiteboard: WhiteboardPlan
    timeline: SceneTimeline
    source_excerpts: tuple[SourceExcerpt, ...]
    schema_version: str = SCENE_PACKAGE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        schema_version = require_text(
            self.schema_version,
            "scene_package.schema_version",
            max_length=32,
        )
        if schema_version not in SUPPORTED_SCENE_PACKAGE_SCHEMA_VERSIONS:
            raise ContractError(
                "scene_package.schema_version must be one of: "
                + ", ".join(
                    sorted(SUPPORTED_SCENE_PACKAGE_SCHEMA_VERSIONS)
                )
            )

        lesson_id = require_id(
            self.lesson_id, "scene_package.lesson_id"
        )
        scene_id = require_id(self.scene_id, "scene_package.scene_id")

        if not isinstance(self.script, SceneScript):
            raise ContractError(
                "scene_package.script must be a SceneScript"
            )
        if not isinstance(self.visual, VisualPlan):
            raise ContractError(
                "scene_package.visual must be a VisualPlan"
            )
        if not isinstance(self.whiteboard, WhiteboardPlan):
            raise ContractError(
                "scene_package.whiteboard must be a WhiteboardPlan"
            )
        if not isinstance(self.timeline, SceneTimeline):
            raise ContractError(
                "scene_package.timeline must be a SceneTimeline"
            )

        source_excerpts = ensure_sequence(
            self.source_excerpts, "scene_package.source_excerpts"
        )
        if not source_excerpts:
            raise ContractError(
                "scene_package.source_excerpts must contain at least one "
                "excerpt"
            )
        if not all(
            isinstance(excerpt, SourceExcerpt)
            for excerpt in source_excerpts
        ):
            raise ContractError(
                "scene_package.source_excerpts must contain SourceExcerpt "
                "objects"
            )
        source_excerpts = tuple(source_excerpts)
        require_unique_source_ids(
            source_excerpts, "scene_package.source_excerpts"
        )

        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "lesson_id", lesson_id)
        object.__setattr__(self, "scene_id", scene_id)
        object.__setattr__(self, "source_excerpts", source_excerpts)

        self._validate_scene_identity()
        self._validate_timeline()
        self._validate_whiteboard_anchors()
        self._validate_sources_and_visual_references()

    def _validate_scene_identity(self) -> None:
        for field_name, component in (
            ("script", self.script),
            ("visual", self.visual),
            ("whiteboard", self.whiteboard),
            ("timeline", self.timeline),
        ):
            if component.scene_id != self.scene_id:
                raise ContractError(
                    f"scene_package.{field_name}.scene_id must match "
                    "scene_package.scene_id"
                )

    def _validate_timeline(self) -> None:
        script_segment_ids = tuple(
            segment.segment_id for segment in self.script.segments
        )
        timeline_segment_ids = tuple(
            entry.segment_id for entry in self.timeline.segments
        )
        if timeline_segment_ids != script_segment_ids:
            raise ContractError(
                "scene_package.timeline.segments segment_id order must match "
                "scene_package.script.segments in order"
            )

        for index, (entry, segment) in enumerate(
            zip(self.timeline.segments, self.script.segments)
        ):
            if not math.isclose(
                entry.duration_seconds,
                segment.estimated_duration_seconds,
                rel_tol=0.0,
                abs_tol=_TIMING_EPSILON,
            ):
                raise ContractError(
                    "scene_package.timeline.segments"
                    f"[{index}].duration_seconds must match "
                    "scene_package.script.segments"
                    f"[{index}].estimated_duration_seconds"
                )

    def _validate_whiteboard_anchors(self) -> None:
        script_segment_ids = {
            segment.segment_id for segment in self.script.segments
        }
        unknown = sorted(
            set(self.whiteboard.segment_anchors) - script_segment_ids
        )
        if unknown:
            raise ContractError(
                "scene_package.whiteboard references unknown narration "
                "segment_id(s): " + ", ".join(unknown)
            )

    def _validate_sources_and_visual_references(self) -> None:
        source_ids = {
            excerpt.source_id for excerpt in self.source_excerpts
        }
        for index, point in enumerate(self.script.key_points):
            unknown = sorted(set(point.source_ids) - source_ids)
            if unknown:
                raise ContractError(
                    "scene_package.script.key_points"
                    f"[{index}].source_ids references source_id(s) not "
                    "present in scene_package.source_excerpts: "
                    + ", ".join(unknown)
                )

        script_segment_ids = {
            segment.segment_id for segment in self.script.segments
        }
        script_key_point_ids = {
            point.key_point_id for point in self.script.key_points
        }
        for index, item in enumerate(self.visual.items):
            unknown_segments = sorted(
                set(item.segment_ids) - script_segment_ids
            )
            if unknown_segments:
                raise ContractError(
                    "scene_package.visual.items"
                    f"[{index}].segment_ids references unknown narration "
                    "segment_id(s): " + ", ".join(unknown_segments)
                )

            unknown_points = sorted(
                set(item.key_point_ids) - script_key_point_ids
            )
            if unknown_points:
                raise ContractError(
                    "scene_package.visual.items"
                    f"[{index}].key_point_ids references unknown "
                    "key_point_id(s): " + ", ".join(unknown_points)
                )

            unknown_sources = sorted(set(item.source_ids) - source_ids)
            if unknown_sources:
                raise ContractError(
                    "scene_package.visual.items"
                    f"[{index}].source_ids references source_id(s) not "
                    "present in scene_package.source_excerpts: "
                    + ", ".join(unknown_sources)
                )

    @classmethod
    def from_dict(cls, data: Any) -> "ScenePackage":
        mapping = require_keys(
            data,
            allowed={
                "schema_version",
                "lesson_id",
                "scene_id",
                "script",
                "visual",
                "whiteboard",
                "timeline",
                "source_excerpts",
            },
            required={
                "lesson_id",
                "scene_id",
                "script",
                "visual",
                "whiteboard",
                "timeline",
                "source_excerpts",
            },
            field="scene_package",
        )
        return cls(
            lesson_id=require_id(
                mapping["lesson_id"], "scene_package.lesson_id"
            ),
            scene_id=require_id(
                mapping["scene_id"], "scene_package.scene_id"
            ),
            script=SceneScript.from_dict(mapping["script"]),
            visual=VisualPlan.from_dict(mapping["visual"]),
            whiteboard=WhiteboardPlan.from_dict(mapping["whiteboard"]),
            timeline=SceneTimeline.from_dict(mapping["timeline"]),
            source_excerpts=source_excerpts_from_sequence(
                mapping["source_excerpts"],
                "scene_package.source_excerpts",
            ),
            schema_version=require_text(
                mapping.get(
                    "schema_version", SCENE_PACKAGE_SCHEMA_VERSION
                ),
                "scene_package.schema_version",
                max_length=32,
            ),
        )


__all__ = [
    "SCENE_PACKAGE_SCHEMA_VERSION",
    "SUPPORTED_SCENE_PACKAGE_SCHEMA_VERSIONS",
    "ScenePackage",
]
