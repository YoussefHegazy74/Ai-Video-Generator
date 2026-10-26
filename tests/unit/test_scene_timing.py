"""Unit tests for deterministic scene timing.

Responsibility: verify cumulative segment timing, duration-bound findings, exact
narration preservation, deterministic construction, and serialization.
Workflow: isolated unit tests; no provider, TTS, network, or filesystem access.
Inputs: ``SceneScript`` values built from the existing fixtures.
Outputs: pytest assertions only.
Interacts with: ``models.scene`` and ``video.scene_timing``.
"""

from __future__ import annotations

import copy

import pytest

from ai_video_generator.models.base import ContractError
from ai_video_generator.models.scene import SceneScript
from ai_video_generator.video.scene_timing import (
    DurationLimits,
    SceneTimeline,
    SegmentTimelineEntry,
    TimingReport,
    TimingSeverity,
    build_scene_timeline,
    validate_duration_bounds,
)


def _single_segment_script(
    *, duration_seconds: float = 10.0, text: str = "Exact narration."
) -> SceneScript:
    return SceneScript.from_dict(
        {
            "scene_id": "scene-001",
            "language": "en",
            "segments": [
                {
                    "segment_id": "seg-1",
                    "text": text,
                    "estimated_duration_seconds": duration_seconds,
                    "key_point_ids": ["kp-1"],
                }
            ],
            "key_points": [
                {
                    "key_point_id": "kp-1",
                    "text": "A grounded teaching point.",
                    "source_ids": ["src-1"],
                }
            ],
        }
    )


def _codes(report: TimingReport) -> set[str]:
    return {finding.code for finding in report.findings}


def test_single_segment_starts_at_zero():
    timeline = build_scene_timeline(
        _single_segment_script(duration_seconds=10.0),
        DurationLimits(5.0, 20.0),
        target_duration_seconds=10.0,
    )

    assert len(timeline.segments) == 1
    entry = timeline.segments[0]
    assert entry.segment_id == "seg-1"
    assert entry.start_seconds == 0.0
    assert entry.end_seconds == pytest.approx(10.0)
    assert entry.duration_seconds == pytest.approx(10.0)


def test_multiple_segments_receive_correct_cumulative_offsets(script_payload):
    script = SceneScript.from_dict(script_payload)

    timeline = build_scene_timeline(script, DurationLimits(1.0, 60.0))

    assert [entry.start_seconds for entry in timeline.segments] == [
        0.0,
        12.0,
        22.0,
    ]
    assert [entry.end_seconds for entry in timeline.segments] == [
        12.0,
        22.0,
        30.0,
    ]
    assert [entry.duration_seconds for entry in timeline.segments] == [
        12.0,
        10.0,
        8.0,
    ]


def test_segment_order_and_ids_are_preserved(script_payload):
    script = SceneScript.from_dict(script_payload)

    timeline = build_scene_timeline(script, DurationLimits(1.0, 60.0))

    assert [entry.segment_id for entry in timeline.segments] == [
        segment.segment_id for segment in script.segments
    ]
    assert [entry.segment_id for entry in timeline.segments] == [
        "seg-1",
        "seg-2",
        "seg-3",
    ]


def test_total_duration_equals_sum_of_segment_estimates(script_payload):
    script = SceneScript.from_dict(script_payload)

    timeline = build_scene_timeline(script, DurationLimits(1.0, 60.0))

    expected = sum(
        segment.estimated_duration_seconds for segment in script.segments
    )
    assert timeline.total_duration_seconds == pytest.approx(expected)
    assert timeline.total_duration_seconds == pytest.approx(
        script.estimated_duration_seconds
    )


def test_exact_narration_text_remains_unchanged(script_payload):
    payload = copy.deepcopy(script_payload)
    payload["segments"][0]["text"] = "  Equation:\n    y = 2x + 1  "
    payload["segments"][1]["text"] = "\n\n  Indented code:\n    return x\n"
    script = SceneScript.from_dict(payload)

    build_scene_timeline(script, DurationLimits(1.0, 60.0))

    assert [segment.text for segment in script.segments] == [
        item["text"] for item in payload["segments"]
    ]


def test_input_script_is_not_mutated(script_payload):
    script = SceneScript.from_dict(copy.deepcopy(script_payload))
    before = script.to_dict()

    build_scene_timeline(script, DurationLimits(1.0, 60.0))

    assert script.to_dict() == before


def test_repeated_calls_produce_identical_results(script_payload):
    script = SceneScript.from_dict(script_payload)
    limits = DurationLimits(1.0, 60.0, tolerance_seconds=0.5)

    first = build_scene_timeline(
        script, limits, target_duration_seconds=30.5
    )
    second = build_scene_timeline(
        script, limits, target_duration_seconds=30.5
    )

    assert first == second
    assert first.to_json() == second.to_json()


def test_total_below_minimum_is_reported(script_payload):
    script = SceneScript.from_dict(script_payload)
    timeline = build_scene_timeline(script, DurationLimits(1.0, 60.0))

    report = validate_duration_bounds(timeline, DurationLimits(40.0, 60.0))

    assert not report.is_valid
    assert "timing.total_below_minimum" in _codes(report)
    assert report.errors[0].severity is TimingSeverity.ERROR


def test_total_above_maximum_is_reported(script_payload):
    script = SceneScript.from_dict(script_payload)
    timeline = build_scene_timeline(script, DurationLimits(1.0, 60.0))

    report = validate_duration_bounds(timeline, DurationLimits(10.0, 20.0))

    assert not report.is_valid
    assert "timing.total_above_maximum" in _codes(report)
    assert report.errors[0].severity is TimingSeverity.ERROR


def test_explicit_ten_second_target_is_handled_when_limits_permit_it():
    script = _single_segment_script(duration_seconds=10.0)

    timeline = build_scene_timeline(
        script,
        DurationLimits(5.0, 20.0),
        target_duration_seconds=10.0,
    )

    assert timeline.target_duration_seconds == pytest.approx(10.0)
    assert timeline.findings == ()
    assert validate_duration_bounds(
        timeline, DurationLimits(5.0, 20.0)
    ).is_valid


def test_no_ten_second_default_is_introduced(script_payload):
    script = SceneScript.from_dict(script_payload)

    timeline = build_scene_timeline(script, DurationLimits(1.0, 60.0))

    assert timeline.target_duration_seconds is None
    assert timeline.findings == ()
    assert "timing.target_mismatch" not in _codes(
        validate_duration_bounds(timeline, DurationLimits(1.0, 60.0))
    )


@pytest.mark.parametrize(
    ("minimum", "maximum", "tolerance"),
    [
        (0.0, 10.0, 0.0),
        (-1.0, 10.0, 0.0),
        (10.0, 5.0, 0.0),
        (float("nan"), 10.0, 0.0),
        (1.0, float("inf"), 0.0),
        (1.0, 10.0, -0.1),
        (1.0, 10.0, float("nan")),
    ],
)
def test_invalid_duration_limits_are_rejected(minimum, maximum, tolerance):
    with pytest.raises(ContractError):
        DurationLimits(minimum, maximum, tolerance)


def test_empty_and_malformed_timing_inputs_are_rejected(script_payload):
    script = SceneScript.from_dict(script_payload)

    with pytest.raises(ContractError, match="at least one entry"):
        SceneTimeline(scene_id="scene-001", segments=())

    with pytest.raises(ContractError, match="SceneScript"):
        build_scene_timeline(object(), DurationLimits(1.0, 60.0))

    with pytest.raises(ContractError, match="DurationLimits"):
        build_scene_timeline(script, object())

    with pytest.raises(ContractError, match="must not be before"):
        SegmentTimelineEntry(
            segment_id="seg-1",
            start_seconds=5.0,
            end_seconds=4.0,
            duration_seconds=1.0,
        )

    with pytest.raises(ContractError, match="must equal"):
        SegmentTimelineEntry(
            segment_id="seg-1",
            start_seconds=0.0,
            end_seconds=2.0,
            duration_seconds=1.0,
        )

    contiguous = SegmentTimelineEntry("seg-1", 0.0, 10.0, 10.0)
    with pytest.raises(ContractError, match="must equal the previous"):
        SceneTimeline(
            scene_id="scene-001",
            segments=(
                contiguous,
                SegmentTimelineEntry("seg-2", 11.0, 20.0, 9.0),
            ),
        )


def test_non_finite_values_are_rejected():
    with pytest.raises(ContractError, match="finite"):
        DurationLimits(float("nan"), 10.0)

    with pytest.raises(ContractError, match="finite"):
        DurationLimits(1.0, float("inf"))

    with pytest.raises(ContractError, match="finite"):
        SegmentTimelineEntry(
            segment_id="seg-1",
            start_seconds=0.0,
            end_seconds=float("inf"),
            duration_seconds=float("inf"),
        )

    entry = SegmentTimelineEntry("seg-1", 0.0, 10.0, 10.0)
    with pytest.raises(ContractError, match="finite"):
        SceneTimeline(
            scene_id="scene-001",
            segments=(entry,),
            target_duration_seconds=float("nan"),
        )


def test_target_duration_mismatch_is_reported_without_changing_entries(
    script_payload,
):
    script = SceneScript.from_dict(script_payload)
    limits = DurationLimits(1.0, 60.0, tolerance_seconds=0.1)

    timeline = build_scene_timeline(
        script, limits, target_duration_seconds=30.5
    )
    report = validate_duration_bounds(timeline, limits)

    assert timeline.total_duration_seconds == pytest.approx(30.0)
    assert timeline.target_duration_seconds == pytest.approx(30.5)
    assert "timing.target_mismatch" in _codes(report)
    assert report.is_valid
    mismatch = next(
        finding
        for finding in report.findings
        if finding.code == "timing.target_mismatch"
    )
    assert mismatch.severity is TimingSeverity.WARNING
    assert "30.500" in mismatch.message
    assert [entry.segment_id for entry in timeline.segments] == [
        "seg-1",
        "seg-2",
        "seg-3",
    ]
    assert [entry.start_seconds for entry in timeline.segments] == [
        0.0,
        12.0,
        22.0,
    ]


def test_target_duration_outside_limits_is_reported(script_payload):
    script = SceneScript.from_dict(script_payload)

    timeline = build_scene_timeline(
        script,
        DurationLimits(10.0, 60.0),
        target_duration_seconds=70.0,
    )
    report = validate_duration_bounds(timeline, DurationLimits(10.0, 60.0))

    assert not report.is_valid
    assert "timing.target_above_maximum" in _codes(report)
    assert "timing.target_mismatch" in _codes(report)


def test_timeline_round_trip_preserves_findings(script_payload):
    script = SceneScript.from_dict(script_payload)
    limits = DurationLimits(1.0, 60.0, tolerance_seconds=0.1)
    timeline = build_scene_timeline(
        script, limits, target_duration_seconds=30.5
    )

    restored = SceneTimeline.from_dict(timeline.to_dict())

    assert restored == timeline
    assert restored.total_duration_seconds == pytest.approx(30.0)
    assert "timing.target_mismatch" in {
        finding.code for finding in restored.findings
    }


def test_timing_report_round_trip(script_payload):
    script = SceneScript.from_dict(script_payload)
    timeline = build_scene_timeline(script, DurationLimits(1.0, 60.0))
    report = validate_duration_bounds(timeline, DurationLimits(40.0, 60.0))

    assert TimingReport.from_dict(report.to_dict()) == report
