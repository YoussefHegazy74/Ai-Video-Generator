"""Semantic validation for visual-planning input and output.

Responsibility: enforce cross-object invariants that the frozen visual contracts
cannot express alone: scene identity and reference existence for narration
segments, key points, and source excerpts.
Workflow: called by ``VisualDesignAgent`` before and after its provider call, and
available to downstream orchestration or review.
Inputs: ``VisualPlanningInput``, ``VisualPlan``, and ``VisualPlanningResult``.
Outputs: ``ValidationReport`` objects with errors and warnings.
Interacts with: the visual-plan models, the shared validation types in
``agents.scene_planning.validation``, and the Example & Visual Agent.
"""

from __future__ import annotations

from typing import Iterable

from ...models.visual_plan import VisualPlan
from ...models.visual_planning import VisualPlanningInput, VisualPlanningResult
from ..scene_planning.validation import (
    IssueSeverity,
    ValidationIssue,
    ValidationReport,
    validate_planning_input,
    validate_script,
)


def _error(code: str, message: str, path: str | None = None) -> ValidationIssue:
    return ValidationIssue(
        severity=IssueSeverity.ERROR,
        code=code,
        message=message,
        path=path,
    )


def _report(issues: Iterable[ValidationIssue]) -> ValidationReport:
    return ValidationReport(issues=tuple(issues))


def validate_visual_input(visual_input: VisualPlanningInput) -> ValidationReport:
    """Validate the scene scope and upstream script before a provider call."""
    if not isinstance(visual_input, VisualPlanningInput):
        return _report(
            [_error("visual_input.type", "visual-planning input has an invalid type")]
        )

    issues: list[ValidationIssue] = []
    issues.extend(
        validate_planning_input(visual_input.planning_input).issues
    )
    issues.extend(
        validate_script(visual_input.script, visual_input.planning_input).issues
    )
    return _report(issues)


def validate_visual_plan(
    plan: VisualPlan,
    visual_input: VisualPlanningInput,
) -> ValidationReport:
    """Validate a visual plan against its scene and available references."""
    if not isinstance(plan, VisualPlan):
        return _report([_error("visual.type", "visual plan has an invalid type")])
    if not isinstance(visual_input, VisualPlanningInput):
        return _report(
            [_error("input.type", "visual-planning input has an invalid type")]
        )

    issues: list[ValidationIssue] = []
    scene_id = visual_input.planning_input.scene.scene_id
    if plan.scene_id != scene_id:
        issues.append(
            _error(
                "visual.scene_id_mismatch",
                f"visual plan scene_id '{plan.scene_id}' does not match input "
                f"scene_id '{scene_id}'",
                "visual_plan.scene_id",
            )
        )

    segment_ids = {
        segment.segment_id for segment in visual_input.script.segments
    }
    key_point_ids = {
        point.key_point_id for point in visual_input.script.key_points
    }
    source_ids = set(visual_input.planning_input.allowed_source_ids)

    for item in plan.items:
        unknown_segments = sorted(set(item.segment_ids) - segment_ids)
        if unknown_segments:
            issues.append(
                _error(
                    "visual.unknown_segment",
                    f"visual item '{item.visual_id}' references unknown "
                    "narration segment_id(s): "
                    + ", ".join(unknown_segments),
                    "visual_plan.items.segment_ids",
                )
            )

        unknown_points = sorted(set(item.key_point_ids) - key_point_ids)
        if unknown_points:
            issues.append(
                _error(
                    "visual.unknown_key_point",
                    f"visual item '{item.visual_id}' references unknown "
                    "key_point_id(s): "
                    + ", ".join(unknown_points),
                    "visual_plan.items.key_point_ids",
                )
            )

        unknown_sources = sorted(set(item.source_ids) - source_ids)
        if unknown_sources:
            issues.append(
                _error(
                    "visual.unknown_source",
                    f"visual item '{item.visual_id}' references source_id(s) "
                    "not provided in the scene: "
                    + ", ".join(unknown_sources),
                    "visual_plan.items.source_ids",
                )
            )

    return _report(issues)


def validate_visual_result(
    result: VisualPlanningResult,
    visual_input: VisualPlanningInput,
) -> ValidationReport:
    """Validate a scene-scoped result and the plan it contains."""
    if not isinstance(result, VisualPlanningResult):
        return _report(
            [_error("visual_result.type", "visual-planning result has an invalid type")]
        )
    if not isinstance(visual_input, VisualPlanningInput):
        return _report(
            [_error("input.type", "visual-planning input has an invalid type")]
        )

    issues: list[ValidationIssue] = []
    scene_id = visual_input.planning_input.scene.scene_id
    if result.scene_id != scene_id:
        issues.append(
            _error(
                "visual_result.scene_id_mismatch",
                f"visual result scene_id '{result.scene_id}' does not match "
                f"input scene_id '{scene_id}'",
                "visual_result.scene_id",
            )
        )
    if result.plan.scene_id != result.scene_id:
        issues.append(
            _error(
                "visual_result.plan_scene_id_mismatch",
                f"visual result scene_id '{result.scene_id}' does not match "
                f"plan scene_id '{result.plan.scene_id}'",
                "visual_result.plan.scene_id",
            )
        )

    issues.extend(validate_visual_plan(result.plan, visual_input).issues)
    return _report(issues)


__all__ = [
    "validate_visual_input",
    "validate_visual_plan",
    "validate_visual_result",
]
