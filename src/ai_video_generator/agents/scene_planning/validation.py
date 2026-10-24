"""Semantic validation for scene-planning inputs and outputs.

Responsibility: enforce cross-object invariants that cannot be expressed by a
single dataclass: source fidelity, narration-to-whiteboard anchoring, continuity,
and practical timing checks.
Workflow: called by each agent after parsing structured output and available to
the future scene-validation/orchestration layer.
Inputs: ``ScenePlanningInput``, ``SceneScript``, ``WhiteboardPlan``, and an
optional previous ``WhiteboardPlan``.
Outputs: ``ValidationReport`` objects with errors and warnings.
Interacts with: both agents, the model/whiteboard contracts, and downstream
validation. Text and code accuracy beyond source-ID grounding is intentionally
left for a future semantic or human review.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from ...models.base import ContractError, ContractModel, require_enum, require_text
from ...models.scene import ScenePlanningInput, SceneScript
from ...whiteboard.drawing_instructions import DrawingActionType
from ...whiteboard.handwriting_plan import HandwritingMode
from ...whiteboard.plan import WhiteboardPlan
from ...whiteboard.visual_continuity import ContinuityMode


class IssueSeverity(str, Enum):
    """Severity of a validation finding."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class ValidationIssue(ContractModel):
    """One actionable validation finding."""

    severity: IssueSeverity
    code: str
    message: str
    path: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.severity, IssueSeverity):
            object.__setattr__(
                self,
                "severity",
                require_enum(self.severity, IssueSeverity, "issue.severity"),
            )
        require_text(self.code, "issue.code", max_length=100)
        require_text(self.message, "issue.message", max_length=2_000)
        if self.path is not None:
            require_text(self.path, "issue.path", max_length=300)


@dataclass(frozen=True, slots=True)
class ValidationReport(ContractModel):
    """Collection of validation findings."""

    issues: tuple[ValidationIssue, ...] = ()

    def __post_init__(self) -> None:
        if not all(isinstance(issue, ValidationIssue) for issue in self.issues):
            raise ContractError("report.issues must contain ValidationIssue objects")
        object.__setattr__(self, "issues", tuple(self.issues))

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity is IssueSeverity.ERROR
        )

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity is IssueSeverity.WARNING
        )

    @property
    def is_valid(self) -> bool:
        return not self.errors

    @property
    def summary(self) -> str:
        if not self.issues:
            return "validation passed with no findings"
        return (
            f"{len(self.errors)} error(s), {len(self.warnings)} warning(s): "
            + "; ".join(issue.message for issue in self.issues)
        )


def _error(code: str, message: str, path: str | None = None) -> ValidationIssue:
    return ValidationIssue(
        severity=IssueSeverity.ERROR, code=code, message=message, path=path
    )


def _warning(code: str, message: str, path: str | None = None) -> ValidationIssue:
    return ValidationIssue(
        severity=IssueSeverity.WARNING, code=code, message=message, path=path
    )


def _report(issues: Iterable[ValidationIssue]) -> ValidationReport:
    return ValidationReport(issues=tuple(issues))


def validate_planning_input(planning_input: ScenePlanningInput) -> ValidationReport:
    """Validate the scene scope before any provider call."""
    if not isinstance(planning_input, ScenePlanningInput):
        return _report([_error("input.type", "planning input has an invalid type")])
    issues: list[ValidationIssue] = []
    if not planning_input.scene.source_excerpts:
        issues.append(
            _error("input.no_sources", "scene has no source excerpts", "scene.source_excerpts")
        )
    if planning_input.target_duration_seconds <= 0:
        issues.append(
            _error(
                "input.invalid_duration",
                "scene target duration must be positive",
                "scene.target_duration_seconds",
            )
        )
    return _report(issues)


def validate_script(
    script: SceneScript,
    planning_input: ScenePlanningInput,
) -> ValidationReport:
    """Validate a script against its scene scope and source excerpts."""
    issues: list[ValidationIssue] = []
    if not isinstance(script, SceneScript):
        return _report([_error("script.type", "script has an invalid type")])
    if not isinstance(planning_input, ScenePlanningInput):
        return _report([_error("input.type", "planning input has an invalid type")])

    if script.scene_id != planning_input.scene.scene_id:
        issues.append(
            _error(
                "script.scene_id_mismatch",
                f"script scene_id '{script.scene_id}' does not match input scene_id "
                f"'{planning_input.scene.scene_id}'",
                "script.scene_id",
            )
        )

    allowed_sources = set(planning_input.allowed_source_ids)
    for point in script.key_points:
        unknown = set(point.source_ids) - allowed_sources
        if unknown:
            issues.append(
                _error(
                    "script.unknown_source_ids",
                    f"key point '{point.key_point_id}' cites source_id(s) not "
                    f"provided in the scene: {', '.join(sorted(unknown))}",
                    "script.key_points",
                )
            )

    if script.estimated_duration_seconds <= 0:
        issues.append(
            _error(
                "script.invalid_duration",
                "script must have a positive estimated duration",
                "script.segments",
            )
        )
    else:
        target = planning_input.target_duration_seconds
        tolerance = max(2.0, target * 0.25)
        if abs(script.estimated_duration_seconds - target) > tolerance:
            issues.append(
                _warning(
                    "script.duration_deviation",
                    f"script duration {script.estimated_duration_seconds:.1f}s differs "
                    f"from scene target {target:.1f}s by more than {tolerance:.1f}s",
                    "script.segments",
                )
            )
        words_per_minute = script.word_count / (
            script.estimated_duration_seconds / 60.0
        )
        if words_per_minute < 70 or words_per_minute > 220:
            issues.append(
                _warning(
                    "script.pacing_outlier",
                    f"narration pace is approximately {words_per_minute:.0f} words per "
                    "minute; verify the estimated segment durations",
                    "script.segments",
                )
            )

    configured_language = planning_input.preferences.language.strip().casefold()
    script_language = script.language.strip().casefold()
    if (
        configured_language
        and script_language
        and configured_language != script_language
        and configured_language not in script_language
        and script_language not in configured_language
    ):
        issues.append(
            _warning(
                "script.language_mismatch",
                f"script language '{script.language}' may not match configured "
                f"language '{planning_input.preferences.language}'",
                "script.language",
            )
        )

    return _report(issues)


def _continuity_issues(
    plan: WhiteboardPlan, previous_plan: WhiteboardPlan | None
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    continuity = plan.continuity
    if previous_plan is None:
        if continuity.mode is not ContinuityMode.FRESH_BOARD:
            issues.append(
                _error(
                    "whiteboard.continuity_without_previous",
                    "a scene without a previous plan must use fresh_board continuity",
                    "whiteboard.continuity.mode",
                )
            )
        if continuity.carry_over_action_ids:
            issues.append(
                _error(
                    "whiteboard.carry_over_without_previous",
                    "no actions can be carried over without a previous plan",
                    "whiteboard.continuity.carry_over_action_ids",
                )
            )
        if continuity.clear_region_ids:
            issues.append(
                _error(
                    "whiteboard.clear_without_previous",
                    "no regions can be cleared without a previous plan",
                    "whiteboard.continuity.clear_region_ids",
                )
            )
        return issues

    if not isinstance(previous_plan, WhiteboardPlan):
        return [
            _error(
                "whiteboard.previous_type",
                "previous_plan must be a WhiteboardPlan or None",
                "whiteboard.previous_plan",
            )
        ]

    if previous_plan.scene_id == plan.scene_id:
        issues.append(
            _warning(
                "whiteboard.previous_scene_same_id",
                "previous plan has the same scene_id as the current plan",
                "whiteboard.previous_plan",
            )
        )

    unknown_carry = set(continuity.carry_over_action_ids) - set(
        previous_plan.drawing.action_ids
    )
    if unknown_carry:
        issues.append(
            _error(
                "whiteboard.unknown_carry_over",
                "carry-over actions are not present in the previous plan: "
                + ", ".join(sorted(unknown_carry)),
                "whiteboard.continuity.carry_over_action_ids",
            )
        )
    unknown_clear = set(continuity.clear_region_ids) - set(
        previous_plan.layout.region_ids
    )
    if unknown_clear:
        issues.append(
            _error(
                "whiteboard.unknown_clear_region",
                "cleared regions are not present in the previous plan: "
                + ", ".join(sorted(unknown_clear)),
                "whiteboard.continuity.clear_region_ids",
            )
        )

    previous_actions = {
        action.action_id: action for action in previous_plan.drawing.actions
    }
    cleared_regions = set(continuity.clear_region_ids)
    for action_id in continuity.carry_over_action_ids:
        action = previous_actions.get(action_id)
        if action is None:
            continue
        if action.action_type is DrawingActionType.ERASE:
            issues.append(
                _error(
                    "whiteboard.carry_over_erase",
                    f"erase action '{action_id}' cannot be carried into the next "
                    "scene because it has no persistent board element",
                    "whiteboard.continuity.carry_over_action_ids",
                )
            )
        if action.region_id in cleared_regions:
            issues.append(
                _error(
                    "whiteboard.carry_over_cleared_region",
                    f"carry-over action '{action_id}' belongs to region "
                    f"'{action.region_id}', which is also cleared",
                    "whiteboard.continuity.carry_over_action_ids",
                )
            )
    return issues


def validate_whiteboard(
    plan: WhiteboardPlan,
    script: SceneScript,
    planning_input: ScenePlanningInput,
    previous_plan: WhiteboardPlan | None = None,
) -> ValidationReport:
    """Validate visual direction against the script, inputs, and continuity."""
    issues: list[ValidationIssue] = []
    if not isinstance(plan, WhiteboardPlan):
        return _report([_error("whiteboard.type", "whiteboard plan has an invalid type")])
    if not isinstance(script, SceneScript):
        return _report([_error("script.type", "script has an invalid type")])
    if not isinstance(planning_input, ScenePlanningInput):
        return _report([_error("input.type", "planning input has an invalid type")])

    if script.scene_id != planning_input.scene.scene_id:
        issues.append(
            _error(
                "whiteboard.script_scene_mismatch",
                "script scene_id does not match the planning input",
                "script.scene_id",
            )
        )
    if plan.scene_id != script.scene_id:
        issues.append(
            _error(
                "whiteboard.scene_id_mismatch",
                f"whiteboard scene_id '{plan.scene_id}' does not match script "
                f"scene_id '{script.scene_id}'",
                "whiteboard.scene_id",
            )
        )

    segment_order = {
        segment.segment_id: index for index, segment in enumerate(script.segments)
    }
    segment_ids = set(segment_order)
    for action in plan.drawing.actions:
        if action.segment_id not in segment_ids:
            issues.append(
                _error(
                    "whiteboard.unknown_segment",
                    f"drawing action '{action.action_id}' references unknown "
                    f"narration segment_id '{action.segment_id}'",
                    "whiteboard.drawing.actions",
                )
            )
    for cue in plan.highlights.cues:
        if cue.segment_id not in segment_ids:
            issues.append(
                _error(
                    "whiteboard.highlight_unknown_segment",
                    f"highlight '{cue.highlight_id}' references unknown narration "
                    f"segment_id '{cue.segment_id}'",
                    "whiteboard.highlights.cues",
                )
            )

    for region_a, region_b in plan.layout.overlapping_pairs():
        issues.append(
            _warning(
                "whiteboard.overlapping_regions",
                f"board regions '{region_a}' and '{region_b}' overlap",
                "whiteboard.layout.regions",
            )
        )

    for cue in plan.handwriting.cues:
        try:
            action = plan.drawing.action_by_id(cue.action_id)
        except ContractError:
            issues.append(
                _error(
                    "whiteboard.unknown_handwriting_action",
                    f"handwriting cue '{cue.cue_id}' references unknown action_id "
                    f"'{cue.action_id}'",
                    "whiteboard.handwriting.cues",
                )
            )
            continue
        if action.action_type is DrawingActionType.WRITE_EQUATION:
            if cue.mode is not HandwritingMode.EQUATION:
                issues.append(
                    _error(
                        "whiteboard.handwriting_mode_mismatch",
                        f"handwriting cue '{cue.cue_id}' must use equation mode",
                        "whiteboard.handwriting.cues",
                    )
                )
        elif action.action_type is DrawingActionType.WRITE_TEXT:
            if cue.mode is HandwritingMode.EQUATION:
                issues.append(
                    _error(
                        "whiteboard.handwriting_mode_mismatch",
                        f"handwriting cue '{cue.cue_id}' cannot use equation mode",
                        "whiteboard.handwriting.cues",
                    )
                )
        else:
            issues.append(
                _error(
                    "whiteboard.handwriting_non_write",
                    f"handwriting cue '{cue.cue_id}' references non-write action "
                    f"'{action.action_id}'",
                    "whiteboard.handwriting.cues",
                )
            )

    for cue in plan.highlights.cues:
        if cue.target_action_id not in plan.drawing.action_ids:
            issues.append(
                _error(
                    "whiteboard.unknown_highlight_target",
                    f"highlight '{cue.highlight_id}' targets unknown action_id "
                    f"'{cue.target_action_id}'",
                    "whiteboard.highlights.cues",
                )
            )
            continue
        target = plan.drawing.action_by_id(cue.target_action_id)
        if target.action_type is DrawingActionType.ERASE:
            issues.append(
                _error(
                    "whiteboard.highlight_erase_target",
                    f"highlight '{cue.highlight_id}' targets erase action "
                    f"'{target.action_id}', which leaves no persistent element",
                    "whiteboard.highlights.cues",
                )
            )
        target_order = segment_order.get(target.segment_id)
        cue_order = segment_order.get(cue.segment_id)
        if (
            target_order is not None
            and cue_order is not None
            and cue_order < target_order
        ):
            issues.append(
                _error(
                    "whiteboard.highlight_before_target",
                    f"highlight '{cue.highlight_id}' is anchored to segment "
                    f"'{cue.segment_id}' before its target action "
                    f"'{target.action_id}' in segment '{target.segment_id}'",
                    "whiteboard.highlights.cues",
                )
            )

    issues.extend(_continuity_issues(plan, previous_plan))

    anchored_segments = set(plan.segment_anchors)
    for segment_id in sorted(segment_ids - anchored_segments):
        issues.append(
            _warning(
                "whiteboard.segment_without_visual",
                f"narration segment '{segment_id}' has no drawing or highlight anchor",
                "whiteboard.drawing.actions",
            )
        )

    if len(plan.drawing.actions) > 50:
        issues.append(
            _warning(
                "whiteboard.action_count",
                "whiteboard plan has more than 50 drawing actions; verify readability "
                "and pacing",
                "whiteboard.drawing.actions",
            )
        )
    for action in plan.drawing.actions:
        if action.action_type in {
            DrawingActionType.WRITE_TEXT,
            DrawingActionType.WRITE_EQUATION,
        } and action.text is not None and len(action.text) > 160:
            issues.append(
                _warning(
                    "whiteboard.long_written_text",
                    f"write action '{action.action_id}' contains more than 160 "
                    "characters; split it into progressive steps",
                    "whiteboard.drawing.actions",
                )
            )

    return _report(issues)


__all__ = [
    "IssueSeverity",
    "ValidationIssue",
    "ValidationReport",
    "validate_planning_input",
    "validate_script",
    "validate_whiteboard",
]
