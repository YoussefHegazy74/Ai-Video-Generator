"""Deterministic Scene Packager implementation.

Responsibility: assemble already-validated scene-planning artifacts into the
canonical provider-neutral ``ScenePackage``.
Workflow: the final deterministic planning step after script, whiteboard,
visual, and timing planning; it runs before provider prompt construction,
rendering, and media generation.
Inputs: a ``ScenePlanningResult`` (scene brief, script, whiteboard, sources),
a ``VisualPlanningResult``, and explicitly configured ``DurationLimits``.
Outputs: a validated ``ScenePackage`` or a typed packaging failure.
Interacts with: ``models.scene_planning``, ``models.visual_planning``,
``video.scene_contract``, and ``video.scene_timing``.

The packager makes no LLM, network, TTS, renderer, or provider calls. It does
not make pedagogical or visual decisions, invent sources, repair references, or
alter narration. Duration policy is explicit: timing findings with error
severity block package creation, while the documented ``timing.target_mismatch``
warning is allowed and retained on the packaged timeline. Timeline values remain
planning estimates, not verified TTS or audio timestamps.
"""

from __future__ import annotations

from typing import Sequence

from ...models.scene_planning import ScenePlanningResult
from ...models.visual_planning import VisualPlanningResult
from ...video.scene_contract import ScenePackage
from ...video.scene_timing import (
    DurationLimits,
    TimingFinding,
    build_scene_timeline,
    validate_duration_bounds,
)


class ScenePackagingError(Exception):
    """Base class for deterministic scene-packaging failures."""

    def __init__(self, message: str, *, details: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.details = tuple(details)


class ScenePackagingInputError(ScenePackagingError):
    """Raised when packaging input is missing, mistyped, or inconsistent."""


class SceneDurationError(ScenePackagingError):
    """Raised when a duration finding blocks package creation.

    ``findings`` contains the blocking error findings. Advisory warnings, such as
    ``timing.target_mismatch``, never raise and remain on the packaged timeline.
    """

    def __init__(
        self,
        message: str,
        *,
        findings: Sequence[TimingFinding],
        details: Sequence[str] = (),
    ) -> None:
        super().__init__(message, details=details)
        self.findings = tuple(findings)


def _require_instance(value: object, expected: type, field: str) -> None:
    if not isinstance(value, expected):
        raise ScenePackagingInputError(
            f"{field} must be a {expected.__name__}, got "
            f"{type(value).__name__}"
        )


def _require_matching_scene_id(
    field: str,
    actual: str,
    expected: str,
) -> None:
    if actual != expected:
        raise ScenePackagingInputError(
            f"{field} '{actual}' must match "
            f"planning_result.scene.scene_id '{expected}'"
        )


class ScenePackager:
    """Stateless, deterministic assembler for one canonical scene package.

    The packager has no constructor dependencies. ``package`` validates the
    top-level artifact types, checks that every component describes the same
    scene, derives the planning timeline from the script, blocks duration-bound
    errors, and delegates cross-reference and structural validation to
    ``ScenePackage``. ``ContractError`` values from that canonical validation
    propagate unchanged rather than being repaired or converted into partial
    output.
    """

    __slots__ = ()

    def package(
        self,
        planning_result: ScenePlanningResult,
        visual_result: VisualPlanningResult,
        limits: DurationLimits,
    ) -> ScenePackage:
        """Assemble and validate the canonical package for one scene.

        Args:
            planning_result: Validated script, whiteboard, scene brief, and
                source excerpts for the scene.
            visual_result: Scene-scoped visual plan from the Example & Visual
                Agent. An empty visual plan is valid.
            limits: Explicit duration bounds used for the planning timeline.

        Returns:
            The canonical ``ScenePackage``.

        Raises:
            ScenePackagingInputError: A required input is missing or mistyped,
                the scene identities disagree, or source context is absent.
            SceneDurationError: The timeline or target duration violates the
                configured duration bounds.
            ContractError: A canonical cross-reference or nested contract
                validation fails. The original contract error is not swallowed.
        """
        _require_instance(planning_result, ScenePlanningResult, "planning_result")
        _require_instance(visual_result, VisualPlanningResult, "visual_result")
        _require_instance(limits, DurationLimits, "limits")

        scene = planning_result.scene
        scene_id = scene.scene_id
        _require_matching_scene_id(
            "planning_result.script.scene_id",
            planning_result.script.scene_id,
            scene_id,
        )
        _require_matching_scene_id(
            "planning_result.whiteboard.scene_id",
            planning_result.whiteboard.scene_id,
            scene_id,
        )
        _require_matching_scene_id(
            "visual_result.scene_id",
            visual_result.scene_id,
            scene_id,
        )
        _require_matching_scene_id(
            "visual_result.plan.scene_id",
            visual_result.plan.scene_id,
            scene_id,
        )

        source_excerpts = scene.source_excerpts
        if not source_excerpts:
            raise ScenePackagingInputError(
                "planning_result.scene.source_excerpts must contain at least "
                "one source excerpt"
            )

        timeline = build_scene_timeline(
            planning_result.script,
            limits,
            target_duration_seconds=scene.target_duration_seconds,
        )
        report = validate_duration_bounds(timeline, limits)
        if report.errors:
            details = tuple(
                f"{finding.code}: {finding.message}"
                for finding in report.errors
            )
            raise SceneDurationError(
                "scene package blocked by duration validation: "
                + "; ".join(details),
                findings=report.errors,
                details=details,
            )

        return ScenePackage(
            lesson_id=planning_result.lesson_id,
            scene_id=scene_id,
            script=planning_result.script,
            visual=visual_result.plan,
            whiteboard=planning_result.whiteboard,
            timeline=timeline,
            source_excerpts=source_excerpts,
        )


__all__ = [
    "SceneDurationError",
    "ScenePackager",
    "ScenePackagingError",
    "ScenePackagingInputError",
]
