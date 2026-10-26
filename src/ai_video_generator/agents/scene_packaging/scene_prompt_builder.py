"""Deterministic provider-neutral scene prompt data builder.

Responsibility: project a validated canonical ``ScenePackage`` into structured,
provider-neutral prompt data for downstream video generation.
Workflow: runs after scene packaging and before any provider-specific prompt
formatting or video-generation integration.
Inputs: one validated ``ScenePackage``.
Outputs: an immutable ``ScenePrompt`` with narration, semantic visual
instructions, whiteboard instructions, source context, and explicitly labeled
planning timing.
Interacts with: ``video.scene_contract``, ``video.scene_timing``,
``models.scene``, ``models.visual_plan``, ``models.source``,
``whiteboard.plan``, and the Scene Packager.

This module does not call an LLM, network, TTS, renderer, or video provider. It
does not summarize, paraphrase, rewrite, or invent educational content. Existing
contracts are composed by reference so narration, formulas, code, labels,
``exact_text``, references, and whiteboard instructions survive unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ...models.base import (
    ContractError,
    ContractModel,
    ensure_sequence,
    require_enum,
    require_id,
    require_keys,
    require_text,
)
from ...models.scene import SceneScript
from ...models.source import (
    SourceExcerpt,
    require_unique_source_ids,
    source_excerpts_from_sequence,
)
from ...models.visual_plan import VisualPlan
from ...video.scene_contract import ScenePackage
from ...video.scene_timing import SceneTimeline
from ...whiteboard.plan import WhiteboardPlan
from .scene_packager_agent import ScenePackagingInputError

SCENE_PROMPT_SCHEMA_VERSION = "1.0"

SUPPORTED_SCENE_PROMPT_SCHEMA_VERSIONS = frozenset(
    {SCENE_PROMPT_SCHEMA_VERSION}
)

SCENE_PROMPT_TIMING_NOTICE = (
    "Timing values are planning estimates derived from segment estimates; "
    "they are not actual TTS or audio timestamps."
)


class PromptTimingBasis(str, Enum):
    """Semantics of the duration values carried by a scene prompt."""

    PLANNING_ESTIMATE = "planning_estimate"


@dataclass(frozen=True, slots=True)
class ScenePromptTiming(ContractModel):
    """Planning timing for a prompt, explicitly separated from audio timing."""

    basis: PromptTimingBasis
    timeline: SceneTimeline

    def __post_init__(self) -> None:
        if not isinstance(self.basis, PromptTimingBasis):
            object.__setattr__(
                self,
                "basis",
                require_enum(
                    self.basis,
                    PromptTimingBasis,
                    "scene_prompt.timing.basis",
                ),
            )
        if not isinstance(self.timeline, SceneTimeline):
            raise ContractError(
                "scene_prompt.timing.timeline must be a SceneTimeline"
            )

    @property
    def estimated_total_duration_seconds(self) -> float:
        """Return the total planning estimate without claiming audio timing."""
        return self.timeline.total_duration_seconds

    @property
    def target_duration_seconds(self) -> float | None:
        """Return the scene's planning target, when available."""
        return self.timeline.target_duration_seconds

    @property
    def notice(self) -> str:
        """Return the fixed human-readable planning-estimate notice."""
        return SCENE_PROMPT_TIMING_NOTICE

    @classmethod
    def from_dict(cls, data: Any) -> "ScenePromptTiming":
        mapping = require_keys(
            data,
            allowed={"basis", "timeline"},
            required={"basis", "timeline"},
            field="scene_prompt.timing",
        )
        return cls(
            basis=require_enum(
                mapping["basis"],
                PromptTimingBasis,
                "scene_prompt.timing.basis",
            ),
            timeline=SceneTimeline.from_dict(mapping["timeline"]),
        )


@dataclass(frozen=True, slots=True)
class ScenePrompt(ContractModel):
    """Provider-neutral structured prompt data for one planned scene.

    The script, visual plan, whiteboard plan, source excerpts, and timeline are
    retained as existing contracts rather than duplicated or flattened. Semantic
    visual instructions remain under ``visual`` and layout/drawing instructions
    remain under ``whiteboard``. The prompt carries no provider name, model name,
    API field, asset URL, renderer instruction, or actual audio timestamp.
    """

    lesson_id: str
    scene_id: str
    script: SceneScript
    visual: VisualPlan
    whiteboard: WhiteboardPlan
    source_excerpts: tuple[SourceExcerpt, ...]
    timing: ScenePromptTiming
    schema_version: str = SCENE_PROMPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        schema_version = require_text(
            self.schema_version,
            "scene_prompt.schema_version",
            max_length=32,
        )
        if schema_version not in SUPPORTED_SCENE_PROMPT_SCHEMA_VERSIONS:
            raise ContractError(
                "scene_prompt.schema_version must be one of: "
                + ", ".join(
                    sorted(SUPPORTED_SCENE_PROMPT_SCHEMA_VERSIONS)
                )
            )

        lesson_id = require_id(
            self.lesson_id, "scene_prompt.lesson_id"
        )
        scene_id = require_id(self.scene_id, "scene_prompt.scene_id")

        if not isinstance(self.script, SceneScript):
            raise ContractError(
                "scene_prompt.script must be a SceneScript"
            )
        if not isinstance(self.visual, VisualPlan):
            raise ContractError(
                "scene_prompt.visual must be a VisualPlan"
            )
        if not isinstance(self.whiteboard, WhiteboardPlan):
            raise ContractError(
                "scene_prompt.whiteboard must be a WhiteboardPlan"
            )
        if not isinstance(self.timing, ScenePromptTiming):
            raise ContractError(
                "scene_prompt.timing must be a ScenePromptTiming"
            )

        source_excerpts = ensure_sequence(
            self.source_excerpts, "scene_prompt.source_excerpts"
        )
        if not source_excerpts:
            raise ContractError(
                "scene_prompt.source_excerpts must contain at least one "
                "excerpt"
            )
        if not all(
            isinstance(excerpt, SourceExcerpt)
            for excerpt in source_excerpts
        ):
            raise ContractError(
                "scene_prompt.source_excerpts must contain SourceExcerpt "
                "objects"
            )
        source_excerpts = tuple(source_excerpts)
        require_unique_source_ids(
            source_excerpts, "scene_prompt.source_excerpts"
        )

        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "lesson_id", lesson_id)
        object.__setattr__(self, "scene_id", scene_id)
        object.__setattr__(self, "source_excerpts", source_excerpts)

        self._validate_against_package()

    def _validate_against_package(self) -> None:
        """Reuse canonical ScenePackage validation instead of duplicating it."""
        try:
            ScenePackage(
                lesson_id=self.lesson_id,
                scene_id=self.scene_id,
                script=self.script,
                visual=self.visual,
                whiteboard=self.whiteboard,
                timeline=self.timing.timeline,
                source_excerpts=self.source_excerpts,
            )
        except ContractError as exc:
            raise ContractError(
                "scene_prompt is inconsistent with the canonical scene "
                f"package: {exc}"
            ) from exc

    @classmethod
    def from_dict(cls, data: Any) -> "ScenePrompt":
        mapping = require_keys(
            data,
            allowed={
                "schema_version",
                "lesson_id",
                "scene_id",
                "script",
                "visual",
                "whiteboard",
                "source_excerpts",
                "timing",
            },
            required={
                "lesson_id",
                "scene_id",
                "script",
                "visual",
                "whiteboard",
                "source_excerpts",
                "timing",
            },
            field="scene_prompt",
        )
        return cls(
            lesson_id=require_id(
                mapping["lesson_id"], "scene_prompt.lesson_id"
            ),
            scene_id=require_id(
                mapping["scene_id"], "scene_prompt.scene_id"
            ),
            script=SceneScript.from_dict(mapping["script"]),
            visual=VisualPlan.from_dict(mapping["visual"]),
            whiteboard=WhiteboardPlan.from_dict(mapping["whiteboard"]),
            source_excerpts=source_excerpts_from_sequence(
                mapping["source_excerpts"],
                "scene_prompt.source_excerpts",
            ),
            timing=ScenePromptTiming.from_dict(mapping["timing"]),
            schema_version=require_text(
                mapping.get(
                    "schema_version", SCENE_PROMPT_SCHEMA_VERSION
                ),
                "scene_prompt.schema_version",
                max_length=32,
            ),
        )


class ScenePromptBuilder:
    """Stateless builder that projects a validated package into prompt data."""

    __slots__ = ()

    def build(self, package: ScenePackage) -> ScenePrompt:
        """Build deterministic prompt data from one canonical package.

        Raises:
            ScenePackagingInputError: ``package`` is not a ``ScenePackage``.
            ContractError: The projected package data fails canonical
                validation.
        """
        if not isinstance(package, ScenePackage):
            raise ScenePackagingInputError(
                "package must be a ScenePackage, got "
                f"{type(package).__name__}"
            )

        return ScenePrompt(
            lesson_id=package.lesson_id,
            scene_id=package.scene_id,
            script=package.script,
            visual=package.visual,
            whiteboard=package.whiteboard,
            source_excerpts=package.source_excerpts,
            timing=ScenePromptTiming(
                basis=PromptTimingBasis.PLANNING_ESTIMATE,
                timeline=package.timeline,
            ),
        )


def build_scene_prompt(package: ScenePackage) -> ScenePrompt:
    """Convenience function for building one provider-neutral scene prompt."""
    return ScenePromptBuilder().build(package)


__all__ = [
    "PromptTimingBasis",
    "SCENE_PROMPT_SCHEMA_VERSION",
    "SCENE_PROMPT_TIMING_NOTICE",
    "SUPPORTED_SCENE_PROMPT_SCHEMA_VERSIONS",
    "ScenePrompt",
    "ScenePromptBuilder",
    "ScenePromptTiming",
    "build_scene_prompt",
]
