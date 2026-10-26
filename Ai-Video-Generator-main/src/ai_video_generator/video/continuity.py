"""Cross-scene structural and style continuity checks."""

from __future__ import annotations

from ai_video_generator.core.constants import CheckStatus, Severity
from ai_video_generator.video.scene_contract import (
    SceneSet,
    ValidationCheck,
    ValidationReport,
)


def validate_scene_set_structure(scene_set: SceneSet) -> ValidationReport:
    checks: list[ValidationCheck] = []
    scenes = scene_set.scenes

    def add(
        check_id: str,
        status: CheckStatus,
        severity: Severity,
        message: str,
        *,
        expected: object = None,
        observed: object = None,
    ) -> None:
        checks.append(
            ValidationCheck(
                check_id=check_id,
                category="continuity",
                status=status,
                severity=severity,
                message=message,
                expected=expected,
                observed=observed,
            )
        )

    ids = [scene.scene_id for scene in scenes]
    if len(ids) == len(set(ids)):
        add(
            "continuity.unique_scene_ids",
            CheckStatus.PASS,
            Severity.INFO,
            "Scene identifiers are unique.",
        )
    else:
        add(
            "continuity.unique_scene_ids",
            CheckStatus.FAIL,
            Severity.CRITICAL,
            "Scene identifiers are duplicated.",
            expected="unique scene IDs",
            observed=ids,
        )

    indices = sorted(scene.scene_index for scene in scenes)
    if len(indices) == len(set(indices)):
        add(
            "continuity.unique_scene_indices",
            CheckStatus.PASS,
            Severity.INFO,
            "Scene indices are unique.",
            observed=indices,
        )
    else:
        add(
            "continuity.unique_scene_indices",
            CheckStatus.FAIL,
            Severity.CRITICAL,
            "Scene indices are duplicated.",
            expected="unique scene indices",
            observed=indices,
        )

    expected_indices = list(range(len(scenes)))
    if indices == expected_indices:
        add(
            "continuity.contiguous_order",
            CheckStatus.PASS,
            Severity.INFO,
            "Scene indices are contiguous and start at zero.",
            expected=expected_indices,
            observed=indices,
        )
    else:
        add(
            "continuity.contiguous_order",
            CheckStatus.FAIL,
            Severity.CRITICAL,
            "Scene indices are missing or non-contiguous; assembly would skip or "
            "misorder scenes.",
            expected=expected_indices,
            observed=indices,
        )

    if scene_set.expected_style is None:
        add(
            "continuity.expected_style",
            CheckStatus.PASS,
            Severity.INFO,
            "No job-level whiteboard style was specified.",
        )
    else:
        mismatched = [
            scene.scene_id
            for scene in scenes
            if scene.visual.whiteboard_style != scene_set.expected_style
        ]
        if mismatched:
            add(
                "continuity.expected_style",
                CheckStatus.FAIL,
                Severity.ERROR,
                "One or more scenes conflict with the job's required whiteboard style.",
                expected=scene_set.expected_style,
                observed=mismatched,
            )
        else:
            add(
                "continuity.expected_style",
                CheckStatus.PASS,
                Severity.INFO,
                "All scenes match the job's required whiteboard style.",
                expected=scene_set.expected_style,
            )

    ordered = scene_set.ordered_scenes
    adjacent_styles = [
        (ordered[index].scene_id, ordered[index + 1].scene_id)
        for index in range(len(ordered) - 1)
        if ordered[index].visual.whiteboard_style
        != ordered[index + 1].visual.whiteboard_style
    ]
    if adjacent_styles:
        add(
            "continuity.adjacent_style",
            CheckStatus.FAIL,
            Severity.ERROR,
            "Adjacent scenes use different whiteboard styles.",
            expected="consistent style across adjacent scenes",
            observed=adjacent_styles,
        )
    else:
        add(
            "continuity.adjacent_style",
            CheckStatus.PASS,
            Severity.INFO,
            "Adjacent scenes use a consistent whiteboard style.",
        )

    add(
        "continuity.semantic_visual_continuity",
        CheckStatus.UNAVAILABLE,
        Severity.INFO,
        "Semantic cross-scene visual continuity is not evaluated automatically.",
    )

    return ValidationReport(
        scene_id="scene_set",
        checks=checks,
        block_on_warnings=False,
    )
