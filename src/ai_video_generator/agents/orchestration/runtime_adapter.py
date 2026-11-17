"""Adapter from planning scene packages to the runtime generation contract.

Responsibility: translate the provider-neutral :class:`ScenePackage` produced by
the Scene Packager into the :class:`SceneSpec`/:class:`SceneSet` structures used
by generation, validation, and assembly.
Workflow: the final handoff after script, whiteboard, visual, and timing planning;
before scene generation.
Inputs: one :class:`ScenePackage` plus its :class:`SceneBrief`, or all packages
for one lesson, and explicit runtime presentation options.
Outputs: a validated ``SceneSpec`` or ``SceneSet`` ready for the workflow
orchestrator.
Interacts with: ``video.scene_contract`` (planning package),
``video.generation_contract`` (runtime scene), ``models.lesson_plan``,
``models.visual_plan``, and ``whiteboard.plan``.

This adapter is deterministic and performs no media, provider, or network work.
It never rewrites narration or drops source provenance. The runtime narration is
the script's ordered segment text, and every source excerpt becomes a runtime
source reference. Text shown on the whiteboard is derived from the whiteboard
write actions and the visual plan's required exact text; the adapter does not
invent teaching content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from ...models.lesson_plan import LessonPlan, SceneBrief
from ...video.generation_contract import (
    SceneSet,
    SceneSpec,
    SourceReference,
    TextElement,
    VisualSpec,
)
from ...video.scene_contract import ScenePackage
from ...whiteboard.drawing_instructions import DrawingActionType

RUNTIME_ADAPTER_VERSION = "1.0"

MAX_RUNTIME_TARGET_SECONDS = 600.0
MIN_RUNTIME_TARGET_SECONDS = 0.1

_RUNTIME_SCENE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

_TEXT_KINDS = {
    DrawingActionType.WRITE_TEXT: "body",
    DrawingActionType.WRITE_EQUATION: "equation",
}


class RuntimeAdapterError(Exception):
    """Raised when a planning artifact cannot be represented at runtime."""

    def __init__(self, message: str, *, details: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.details = tuple(details)


@dataclass(frozen=True, slots=True)
class RuntimeSceneOptions:
    """Explicit runtime presentation settings applied to every adapted scene.

    The whiteboard style is a runtime/presentation property; teaching style is a
    separate pedagogical preference owned by planning.
    """

    whiteboard_style: str = "clean_whiteboard"
    background_color: str = "#F8FAFC"
    accent_color: str = "#2563EB"
    show_scene_title: bool = True

    def __post_init__(self) -> None:
        if not self.whiteboard_style.strip():
            raise RuntimeAdapterError("whiteboard_style must not be blank")
        for name, value in (
            ("background_color", self.background_color),
            ("accent_color", self.accent_color),
        ):
            if not _HEX_COLOR_RE.fullmatch(value):
                raise RuntimeAdapterError(f"{name} must use #RRGGBB format")


def _require_runtime_scene_id(scene_id: str, field: str) -> None:
    if not _RUNTIME_SCENE_ID_RE.fullmatch(scene_id):
        raise RuntimeAdapterError(
            f"{field} {scene_id!r} is not a runtime-safe scene identifier; "
            "runtime IDs are 1-64 characters of letters, digits, dot, "
            "underscore, or hyphen and must start with a letter or digit",
            details=["scene_id", scene_id],
        )


def _drawing_step(action) -> str:
    payload = action.text or action.label or action.action_type.value
    return f"{action.order}. {action.action_type.value}: {payload}"


class SceneRuntimeAdapter:
    """Deterministic planning-to-runtime adapter for one lesson."""

    __slots__ = ()

    def adapt_scene(
        self,
        package: ScenePackage,
        brief: SceneBrief,
        *,
        scene_index: int,
        options: RuntimeSceneOptions | None = None,
    ) -> SceneSpec:
        """Translate one validated package into a runtime ``SceneSpec``.

        Raises:
            RuntimeAdapterError: identities disagree, the scene ID is not
                runtime-safe, or the planned duration is outside the runtime
                contract bounds.
        """
        if not isinstance(package, ScenePackage):
            raise RuntimeAdapterError("package must be a ScenePackage")
        if not isinstance(brief, SceneBrief):
            raise RuntimeAdapterError("brief must be a SceneBrief")
        if isinstance(scene_index, bool) or not isinstance(scene_index, int):
            raise RuntimeAdapterError("scene_index must be an integer")
        if scene_index < 0:
            raise RuntimeAdapterError("scene_index must not be negative")
        options = options or RuntimeSceneOptions()

        if package.scene_id != brief.scene_id:
            raise RuntimeAdapterError(
                "package.scene_id must match brief.scene_id"
            )
        _require_runtime_scene_id(package.scene_id, "package.scene_id")

        duration = package.timeline.total_duration_seconds
        if not MIN_RUNTIME_TARGET_SECONDS <= duration <= MAX_RUNTIME_TARGET_SECONDS:
            raise RuntimeAdapterError(
                "planned duration is outside the runtime contract bounds: "
                f"{duration:.3f}s not in "
                f"[{MIN_RUNTIME_TARGET_SECONDS}, {MAX_RUNTIME_TARGET_SECONDS}]",
                details=["target_duration_seconds", str(duration)],
            )

        return SceneSpec(
            scene_id=package.scene_id,
            scene_index=scene_index,
            title=brief.title,
            learning_objective=brief.objective,
            section_title=self._section_title(package, brief),
            source_references=self._source_references(package),
            narration=package.script.narration,
            visual=self._visual_spec(package, brief, options),
            target_duration_seconds=duration,
        )

    def adapt_lesson(
        self,
        packages: Sequence[ScenePackage],
        lesson_plan: LessonPlan,
        *,
        options: RuntimeSceneOptions | None = None,
    ) -> SceneSet:
        """Translate every planned scene of one lesson into an ordered scene set.

        Lesson scenes are ordered by their planning ``sequence``. Runtime scene
        indices are assigned from that order and are always contiguous from 0.

        Raises:
            RuntimeAdapterError: a lesson scene has no package, a package does
                not belong to the lesson, or a scene cannot be adapted.
        """
        if not isinstance(lesson_plan, LessonPlan):
            raise RuntimeAdapterError("lesson_plan must be a LessonPlan")
        options = options or RuntimeSceneOptions()

        ordered_packages: dict[str, ScenePackage] = {}
        for package in packages:
            if not isinstance(package, ScenePackage):
                raise RuntimeAdapterError(
                    "packages must contain ScenePackage objects"
                )
            if package.scene_id in ordered_packages:
                raise RuntimeAdapterError(
                    f"duplicate package for scene {package.scene_id!r}"
                )
            ordered_packages[package.scene_id] = package

        lesson_scene_ids = [scene.scene_id for scene in lesson_plan.scenes]
        missing = [sid for sid in lesson_scene_ids if sid not in ordered_packages]
        if missing:
            raise RuntimeAdapterError(
                "no scene package was produced for lesson scene(s): "
                + ", ".join(sorted(missing)),
                details=missing,
            )
        extra = sorted(set(ordered_packages) - set(lesson_scene_ids))
        if extra:
            raise RuntimeAdapterError(
                "scene package(s) do not belong to the lesson: "
                + ", ".join(extra),
                details=extra,
            )

        scenes = [
            self.adapt_scene(
                ordered_packages[brief.scene_id],
                brief,
                scene_index=index,
                options=options,
            )
            for index, brief in enumerate(lesson_plan.scenes)
        ]
        return SceneSet(
            project_id=lesson_plan.lesson_id,
            course_title=lesson_plan.title,
            expected_style=options.whiteboard_style,
            scenes=scenes,
        )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _section_title(
        package: ScenePackage, brief: SceneBrief
    ) -> str | None:
        """Return the first source section label unless it repeats the title.

        Planning does not define a dedicated scene-section field. The first
        excerpt's section is the only stable signal; later excerpts may come
        from other sections and must not be mislabeled as this scene's section.
        """
        if not package.source_excerpts:
            return None
        section = package.source_excerpts[0].reference.section_title
        if not section:
            return None
        if section.strip().casefold() == brief.title.strip().casefold():
            return None
        return section

    @staticmethod
    def _source_references(package: ScenePackage) -> list[SourceReference]:
        references: list[SourceReference] = []
        for excerpt in package.source_excerpts:
            fallback = excerpt.reference.anchor or excerpt.reference.section_title
            references.append(
                SourceReference(
                    document_id=excerpt.reference.document_id,
                    page_numbers=[excerpt.reference.page_number],
                    reference=fallback,
                    excerpt=excerpt.text,
                )
            )
        return references

    def _visual_spec(
        self,
        package: ScenePackage,
        brief: SceneBrief,
        options: RuntimeSceneOptions,
    ) -> VisualSpec:
        whiteboard = package.whiteboard
        highlighted_actions = {
            cue.target_action_id for cue in whiteboard.highlights.cues
        }

        text_elements: list[TextElement] = []
        seen_texts: set[str] = set()

        def add_text(text: str, kind: str, *, emphasis: bool = False) -> None:
            if text in seen_texts:
                return
            seen_texts.add(text)
            text_elements.append(
                TextElement(text=text, kind=kind, emphasis=emphasis)
            )

        for action in whiteboard.drawing.actions:
            kind = _TEXT_KINDS.get(action.action_type)
            if kind is None or not action.text:
                continue
            add_text(
                action.text,
                kind,
                emphasis=action.action_id in highlighted_actions
                or bool(action.label and "emphasis" in action.label.lower()),
            )

        for item in package.visual.items:
            for exact_text in item.exact_text:
                add_text(exact_text, "body")

        instructions = [
            item.description for item in package.visual.items
        ] + list(whiteboard.presentation_notes)
        instructions = list(dict.fromkeys(
            value.strip() for value in instructions if value.strip()
        ))
        if not instructions:
            instructions = [
                f"Teach {brief.title} on a clean whiteboard using the "
                "authoritative narration."
            ]

        drawing_steps = [
            _drawing_step(action) for action in whiteboard.drawing.actions
        ]

        return VisualSpec(
            whiteboard_style=options.whiteboard_style,
            background_color=options.background_color,
            accent_color=options.accent_color,
            instructions=instructions,
            text_elements=text_elements,
            drawing_steps=drawing_steps,
            show_scene_title=options.show_scene_title,
        )


__all__ = [
    "MAX_RUNTIME_TARGET_SECONDS",
    "MIN_RUNTIME_TARGET_SECONDS",
    "RUNTIME_ADAPTER_VERSION",
    "RuntimeAdapterError",
    "RuntimeSceneOptions",
    "SceneRuntimeAdapter",
]
