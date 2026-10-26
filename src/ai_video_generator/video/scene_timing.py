"""Deterministic scene timing contracts and planning helpers.

Responsibility: convert a validated ``SceneScript`` into an ordered, cumulative
segment timeline and report duration-bound violations without changing narration.
Workflow: planning and packaging foundation; the future Scene Packager can use
the timeline to attach visual events to narration segments.
Inputs: a validated ``SceneScript``, explicitly configured ``DurationLimits``,
and an optional target duration supplied by the caller (for example from
``SceneBrief.target_duration_seconds``).
Outputs: immutable ``SceneTimeline`` and ``TimingReport`` objects with precise
timing findings.
Interacts with: ``models.scene``, the future Scene Packager, validation, and
assembly. No provider, TTS, LLM, network, or rendering behavior belongs here.

Timing values in this module are planning estimates, not verified TTS audio
timestamps. The module never truncates, rewrites, stretches, or removes
narration. Duration violations are reported, never silently fixed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from ..models.base import (
    ContractError,
    ContractModel,
    ensure_sequence,
    optional_number,
    require_enum,
    require_id,
    require_keys,
    require_number,
    require_text,
    require_unique,
)
from ..models.scene import SceneScript

SCENE_TIMELINE_SCHEMA_VERSION = "1.0"

SUPPORTED_SCENE_TIMELINE_SCHEMA_VERSIONS = frozenset(
    {SCENE_TIMELINE_SCHEMA_VERSION}
)

# Floating-point tolerance for cumulative offsets and derived totals. It is not
# a content tolerance and never permits truncating or stretching narration.
_TIMING_EPSILON = 1e-6


@dataclass(frozen=True, slots=True)
class DurationLimits(ContractModel):
    """Explicitly configured scene-duration constraints.

    There is deliberately no default duration in this contract. Callers must
    supply the values appropriate to the current policy or provider.
    """

    minimum_seconds: float
    maximum_seconds: float
    tolerance_seconds: float = 0.0

    def __post_init__(self) -> None:
        minimum = require_number(self.minimum_seconds, "limits.minimum_seconds")
        if minimum <= 0:
            raise ContractError("limits.minimum_seconds must be positive")
        maximum = require_number(self.maximum_seconds, "limits.maximum_seconds")
        if maximum < minimum:
            raise ContractError(
                "limits.maximum_seconds must be greater than or equal to "
                "limits.minimum_seconds"
            )
        tolerance = require_number(
            self.tolerance_seconds,
            "limits.tolerance_seconds",
            min_value=0.0,
        )
        object.__setattr__(self, "minimum_seconds", minimum)
        object.__setattr__(self, "maximum_seconds", maximum)
        object.__setattr__(self, "tolerance_seconds", tolerance)

    @classmethod
    def from_dict(cls, data: Any) -> "DurationLimits":
        mapping = require_keys(
            data,
            allowed={"minimum_seconds", "maximum_seconds", "tolerance_seconds"},
            required={"minimum_seconds", "maximum_seconds"},
            field="duration_limits",
        )
        return cls(
            minimum_seconds=require_number(
                mapping["minimum_seconds"], "limits.minimum_seconds"
            ),
            maximum_seconds=require_number(
                mapping["maximum_seconds"], "limits.maximum_seconds"
            ),
            tolerance_seconds=require_number(
                mapping.get("tolerance_seconds", 0.0),
                "limits.tolerance_seconds",
                min_value=0.0,
            ),
        )


@dataclass(frozen=True, slots=True)
class SegmentTimelineEntry(ContractModel):
    """Planned absolute timing for one narration segment."""

    segment_id: str
    start_seconds: float
    end_seconds: float
    duration_seconds: float

    def __post_init__(self) -> None:
        segment_id = require_id(self.segment_id, "timeline.segment.segment_id")
        start = require_number(
            self.start_seconds,
            "timeline.segment.start_seconds",
            min_value=0.0,
        )
        end = require_number(
            self.end_seconds,
            "timeline.segment.end_seconds",
            min_value=0.0,
        )
        duration = require_number(
            self.duration_seconds,
            "timeline.segment.duration_seconds",
            min_value=0.0,
        )
        if duration <= 0:
            raise ContractError(
                "timeline.segment.duration_seconds must be positive"
            )
        if end < start:
            raise ContractError(
                "timeline.segment.end_seconds must not be before start_seconds"
            )
        if not math.isclose(
            end - start,
            duration,
            rel_tol=0.0,
            abs_tol=_TIMING_EPSILON,
        ):
            raise ContractError(
                "timeline.segment.duration_seconds must equal "
                "end_seconds - start_seconds"
            )
        object.__setattr__(self, "segment_id", segment_id)
        object.__setattr__(self, "start_seconds", start)
        object.__setattr__(self, "end_seconds", end)
        object.__setattr__(self, "duration_seconds", duration)

    @classmethod
    def from_dict(cls, data: Any) -> "SegmentTimelineEntry":
        mapping = require_keys(
            data,
            allowed={
                "segment_id",
                "start_seconds",
                "end_seconds",
                "duration_seconds",
            },
            required={
                "segment_id",
                "start_seconds",
                "end_seconds",
                "duration_seconds",
            },
            field="timeline.segment",
        )
        return cls(
            segment_id=require_id(
                mapping["segment_id"], "timeline.segment.segment_id"
            ),
            start_seconds=require_number(
                mapping["start_seconds"],
                "timeline.segment.start_seconds",
                min_value=0.0,
            ),
            end_seconds=require_number(
                mapping["end_seconds"],
                "timeline.segment.end_seconds",
                min_value=0.0,
            ),
            duration_seconds=require_number(
                mapping["duration_seconds"],
                "timeline.segment.duration_seconds",
                min_value=0.0,
            ),
        )


class TimingSeverity(str, Enum):
    """Severity of one timing validation finding."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class TimingFinding(ContractModel):
    """One actionable timing finding with a stable code and message."""

    severity: TimingSeverity
    code: str
    message: str
    path: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.severity, TimingSeverity):
            object.__setattr__(
                self,
                "severity",
                require_enum(
                    self.severity,
                    TimingSeverity,
                    "timing_finding.severity",
                ),
            )
        require_text(self.code, "timing_finding.code", max_length=100)
        require_text(self.message, "timing_finding.message", max_length=2_000)
        if self.path is not None:
            require_text(self.path, "timing_finding.path", max_length=300)

    @classmethod
    def from_dict(cls, data: Any) -> "TimingFinding":
        mapping = require_keys(
            data,
            allowed={"severity", "code", "message", "path"},
            required={"severity", "code", "message"},
            field="timing_finding",
        )
        path = mapping.get("path")
        return cls(
            severity=require_enum(
                mapping["severity"], TimingSeverity, "timing_finding.severity"
            ),
            code=require_text(
                mapping["code"], "timing_finding.code", max_length=100
            ),
            message=require_text(
                mapping["message"], "timing_finding.message", max_length=2_000
            ),
            path=(
                None
                if path is None
                else require_text(
                    path, "timing_finding.path", max_length=300
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class TimingReport(ContractModel):
    """Collection of timing findings; warnings do not invalidate the timeline."""

    findings: tuple[TimingFinding, ...] = ()

    def __post_init__(self) -> None:
        findings = ensure_sequence(self.findings, "timing_report.findings")
        if not all(isinstance(finding, TimingFinding) for finding in findings):
            raise ContractError(
                "timing_report.findings must contain TimingFinding objects"
            )
        object.__setattr__(self, "findings", tuple(findings))

    @property
    def errors(self) -> tuple[TimingFinding, ...]:
        return tuple(
            finding
            for finding in self.findings
            if finding.severity is TimingSeverity.ERROR
        )

    @property
    def warnings(self) -> tuple[TimingFinding, ...]:
        return tuple(
            finding
            for finding in self.findings
            if finding.severity is TimingSeverity.WARNING
        )

    @property
    def is_valid(self) -> bool:
        return not self.errors

    @property
    def summary(self) -> str:
        if not self.findings:
            return "timing validation passed with no findings"
        return (
            f"{len(self.errors)} error(s), {len(self.warnings)} warning(s): "
            + "; ".join(finding.message for finding in self.findings)
        )

    @classmethod
    def from_dict(cls, data: Any) -> "TimingReport":
        mapping = require_keys(
            data,
            allowed={"findings"},
            required=set(),
            field="timing_report",
        )
        finding_items = ensure_sequence(
            mapping.get("findings", []), "timing_report.findings"
        )
        return cls(
            findings=tuple(
                TimingFinding.from_dict(item) for item in finding_items
            )
        )


@dataclass(frozen=True, slots=True)
class SceneTimeline(ContractModel):
    """Ordered, contiguous planned timeline for one scene.

    ``total_duration_seconds`` is derived from the entries so it cannot drift
    from them. It is a planning estimate and is intentionally not stored as a
    second serialized field.
    """

    scene_id: str
    segments: tuple[SegmentTimelineEntry, ...]
    target_duration_seconds: float | None = None
    findings: tuple[TimingFinding, ...] = ()
    schema_version: str = SCENE_TIMELINE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version not in SUPPORTED_SCENE_TIMELINE_SCHEMA_VERSIONS:
            raise ContractError(
                "timeline.schema_version must be one of: "
                + ", ".join(sorted(SUPPORTED_SCENE_TIMELINE_SCHEMA_VERSIONS))
            )
        scene_id = require_id(self.scene_id, "timeline.scene_id")

        segments = ensure_sequence(self.segments, "timeline.segments")
        if not segments:
            raise ContractError(
                "timeline.segments must contain at least one entry"
            )
        if not all(
            isinstance(entry, SegmentTimelineEntry) for entry in segments
        ):
            raise ContractError(
                "timeline.segments must contain SegmentTimelineEntry objects"
            )
        segments = tuple(segments)
        require_unique(
            [entry.segment_id for entry in segments],
            "timeline.segments.segment_id",
        )

        if not math.isclose(
            segments[0].start_seconds,
            0.0,
            rel_tol=0.0,
            abs_tol=_TIMING_EPSILON,
        ):
            raise ContractError(
                "timeline.segments[0].start_seconds must be 0.0"
            )
        for index, (previous, current) in enumerate(
            zip(segments, segments[1:]), start=1
        ):
            if not math.isclose(
                current.start_seconds,
                previous.end_seconds,
                rel_tol=0.0,
                abs_tol=_TIMING_EPSILON,
            ):
                raise ContractError(
                    f"timeline.segments[{index}].start_seconds must equal "
                    "the previous entry's end_seconds"
                )

        target = self.target_duration_seconds
        if target is not None:
            target = require_number(
                target,
                "timeline.target_duration_seconds",
                min_value=0.0,
            )
            if target <= 0:
                raise ContractError(
                    "timeline.target_duration_seconds must be positive"
                )

        findings = ensure_sequence(self.findings, "timeline.findings")
        if not all(
            isinstance(finding, TimingFinding) for finding in findings
        ):
            raise ContractError(
                "timeline.findings must contain TimingFinding objects"
            )

        object.__setattr__(self, "scene_id", scene_id)
        object.__setattr__(self, "segments", segments)
        object.__setattr__(self, "target_duration_seconds", target)
        object.__setattr__(self, "findings", tuple(findings))

    @property
    def total_duration_seconds(self) -> float:
        """Return the unrounded sum of all segment estimates."""
        return sum(entry.duration_seconds for entry in self.segments)

    @classmethod
    def from_dict(cls, data: Any) -> "SceneTimeline":
        mapping = require_keys(
            data,
            allowed={
                "scene_id",
                "segments",
                "target_duration_seconds",
                "findings",
                "schema_version",
            },
            required={"scene_id", "segments"},
            field="timeline",
        )
        segment_items = ensure_sequence(
            mapping["segments"], "timeline.segments"
        )
        finding_items = ensure_sequence(
            mapping.get("findings", []), "timeline.findings"
        )
        return cls(
            scene_id=require_id(mapping["scene_id"], "timeline.scene_id"),
            segments=tuple(
                SegmentTimelineEntry.from_dict(item)
                for item in segment_items
            ),
            target_duration_seconds=optional_number(
                mapping.get("target_duration_seconds"),
                "timeline.target_duration_seconds",
                min_value=0.0,
            ),
            findings=tuple(
                TimingFinding.from_dict(item) for item in finding_items
            ),
            schema_version=require_text(
                mapping.get(
                    "schema_version", SCENE_TIMELINE_SCHEMA_VERSION
                ),
                "timeline.schema_version",
                max_length=32,
            ),
        )


def _error(code: str, message: str, path: str | None = None) -> TimingFinding:
    return TimingFinding(
        severity=TimingSeverity.ERROR,
        code=code,
        message=message,
        path=path,
    )


def _warning(
    code: str, message: str, path: str | None = None
) -> TimingFinding:
    return TimingFinding(
        severity=TimingSeverity.WARNING,
        code=code,
        message=message,
        path=path,
    )


def build_scene_timeline(
    script: SceneScript,
    limits: DurationLimits,
    *,
    target_duration_seconds: float | None = None,
) -> SceneTimeline:
    """Build a deterministic, contiguous timeline from a validated script.

    ``SceneScript`` does not carry the scene target duration, so callers that
    have a ``SceneBrief`` should pass ``SceneBrief.target_duration_seconds``
    explicitly. The target is optional and is never inferred or defaulted.

    The function preserves segment order, IDs, and exact narration text. It
    never mutates ``script`` and never calls an external service.
    """
    if not isinstance(script, SceneScript):
        raise ContractError("script must be a SceneScript")
    if not isinstance(limits, DurationLimits):
        raise ContractError("limits must be a DurationLimits")

    target: float | None = None
    if target_duration_seconds is not None:
        target = require_number(
            target_duration_seconds,
            "timeline.target_duration_seconds",
        )
        if target <= 0:
            raise ContractError(
                "timeline.target_duration_seconds must be positive"
            )

    entries: list[SegmentTimelineEntry] = []
    start_seconds = 0.0
    for segment in script.segments:
        duration = segment.estimated_duration_seconds
        end_seconds = start_seconds + duration
        entries.append(
            SegmentTimelineEntry(
                segment_id=segment.segment_id,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                duration_seconds=duration,
            )
        )
        start_seconds = end_seconds

    timeline = SceneTimeline(
        scene_id=script.scene_id,
        segments=tuple(entries),
        target_duration_seconds=target,
    )
    report = validate_duration_bounds(timeline, limits)
    if report.findings:
        timeline = replace(timeline, findings=report.findings)
    return timeline


def validate_duration_bounds(
    timeline: SceneTimeline,
    limits: DurationLimits,
) -> TimingReport:
    """Return explicit findings for duration-bound and target mismatches.

    The timeline is never modified. Total duration outside the configured
    minimum or maximum is an error. A target outside the configured bounds is
    also an error. A target that differs from the estimated total by more than
    the configured tolerance is a warning, matching the advisory duration
    warnings used elsewhere in scene planning.
    """
    if not isinstance(timeline, SceneTimeline):
        raise ContractError("timeline must be a SceneTimeline")
    if not isinstance(limits, DurationLimits):
        raise ContractError("limits must be a DurationLimits")

    total = timeline.total_duration_seconds
    findings: list[TimingFinding] = []

    if total + _TIMING_EPSILON < limits.minimum_seconds:
        findings.append(
            _error(
                "timing.total_below_minimum",
                f"timeline total {total:.3f}s is below the configured minimum "
                f"{limits.minimum_seconds:.3f}s",
                "timeline.segments",
            )
        )
    if total - _TIMING_EPSILON > limits.maximum_seconds:
        findings.append(
            _error(
                "timing.total_above_maximum",
                f"timeline total {total:.3f}s is above the configured maximum "
                f"{limits.maximum_seconds:.3f}s",
                "timeline.segments",
            )
        )

    target = timeline.target_duration_seconds
    if target is not None:
        if target + _TIMING_EPSILON < limits.minimum_seconds:
            findings.append(
                _error(
                    "timing.target_below_minimum",
                    f"target duration {target:.3f}s is below the configured "
                    f"minimum {limits.minimum_seconds:.3f}s",
                    "timeline.target_duration_seconds",
                )
            )
        if target - _TIMING_EPSILON > limits.maximum_seconds:
            findings.append(
                _error(
                    "timing.target_above_maximum",
                    f"target duration {target:.3f}s is above the configured "
                    f"maximum {limits.maximum_seconds:.3f}s",
                    "timeline.target_duration_seconds",
                )
            )

        mismatch = abs(total - target)
        if mismatch > limits.tolerance_seconds + _TIMING_EPSILON:
            findings.append(
                _warning(
                    "timing.target_mismatch",
                    f"timeline total {total:.3f}s differs from target "
                    f"{target:.3f}s by {mismatch:.3f}s, which exceeds the "
                    f"configured tolerance {limits.tolerance_seconds:.3f}s",
                    "timeline.target_duration_seconds",
                )
            )

    return TimingReport(findings=tuple(findings))


__all__ = [
    "DurationLimits",
    "SCENE_TIMELINE_SCHEMA_VERSION",
    "SUPPORTED_SCENE_TIMELINE_SCHEMA_VERSIONS",
    "SceneTimeline",
    "SegmentTimelineEntry",
    "TimingFinding",
    "TimingReport",
    "TimingSeverity",
    "build_scene_timeline",
    "validate_duration_bounds",
]
