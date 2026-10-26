from __future__ import annotations

from pathlib import Path

import pytest

from ai_video_generator.agents.validation.scene_validation_agent import (
    SceneValidationAgent,
)
from ai_video_generator.core.constants import CheckStatus, SceneStatus
from ai_video_generator.services.storage_service import ArtifactStorageService
from ai_video_generator.video.audio_video_validation import (
    SceneValidator,
    ValidationPolicy,
)
from ai_video_generator.video.media_probe import FFmpegTools
from ai_video_generator.video.scene_contract import SceneSpec

from tests.helpers import (
    create_test_clip,
    make_generated_scene,
    make_scene,
)


def _generated_scene(
    artifact_storage: ArtifactStorageService,
    media_tools: FFmpegTools,
    tmp_path: Path,
    *,
    scene_id: str = "scene-1",
    target_duration: float = 1.0,
    silent: bool = False,
    audio: bool = True,
    manifest_texts: list[str] | None = None,
) -> SceneSpec:
    clip = create_test_clip(
        tmp_path / f"{scene_id}.mp4",
        media_tools,
        duration=1.0,
        audio=audio,
        silent_audio=silent,
    )
    scene = make_scene(scene_id, target_duration=target_duration)
    return make_generated_scene(
        scene,
        artifact_storage,
        media_tools,
        clip,
        with_manifest=True,
        manifest_texts=manifest_texts,
    )


def _validator(
    media_tools: FFmpegTools,
    artifact_storage: ArtifactStorageService,
    policy: ValidationPolicy,
) -> SceneValidator:
    return SceneValidator(media_tools, artifact_storage, policy=policy)


def _check(report, check_id: str):
    matches = [check for check in report.checks if check.check_id == check_id]
    assert len(matches) == 1
    return matches[0]


def test_validator_passes_measurable_checks(
    artifact_storage: ArtifactStorageService,
    media_tools: FFmpegTools,
    validation_policy: ValidationPolicy,
    tmp_path: Path,
) -> None:
    scene = _generated_scene(artifact_storage, media_tools, tmp_path)
    report = _validator(media_tools, artifact_storage, validation_policy).validate(
        scene,
        expected_style="clean_whiteboard",
        scene_ids=[scene.scene_id],
        total_scenes=1,
    )

    assert report.passed
    assert report.blocking_failure_count == 0
    assert _check(report, "duration.target_delta").status == CheckStatus.PASS
    assert _check(report, "text.accuracy").status == CheckStatus.PASS
    assert _check(report, "sync.audio_not_silent").status == CheckStatus.PASS
    assert _check(report, "safety.video_integrity").status == CheckStatus.PASS
    assert _check(report, "sync.semantic_alignment").status == CheckStatus.UNAVAILABLE
    assert _check(report, "safety.source_grounding").status == CheckStatus.UNAVAILABLE


def test_duration_outside_failure_tolerance_blocks(
    artifact_storage: ArtifactStorageService,
    media_tools: FFmpegTools,
    validation_policy: ValidationPolicy,
    tmp_path: Path,
) -> None:
    scene = _generated_scene(
        artifact_storage,
        media_tools,
        tmp_path,
        target_duration=5.0,
    )
    report = _validator(media_tools, artifact_storage, validation_policy).validate(
        scene
    )

    check = _check(report, "duration.target_delta")
    assert check.status == CheckStatus.FAIL
    assert report.is_blocked


def test_silent_narration_blocks_and_recommends_retry(
    artifact_storage: ArtifactStorageService,
    media_tools: FFmpegTools,
    validation_policy: ValidationPolicy,
    tmp_path: Path,
) -> None:
    scene = _generated_scene(
        artifact_storage,
        media_tools,
        tmp_path,
        silent=True,
    )
    report = _validator(media_tools, artifact_storage, validation_policy).validate(
        scene
    )

    check = _check(report, "sync.audio_not_silent")
    assert check.status == CheckStatus.FAIL
    assert check.retryable
    assert report.retry_recommended


def test_missing_audio_stream_blocks(
    artifact_storage: ArtifactStorageService,
    media_tools: FFmpegTools,
    validation_policy: ValidationPolicy,
    tmp_path: Path,
) -> None:
    scene = _generated_scene(
        artifact_storage,
        media_tools,
        tmp_path,
        audio=False,
    )
    report = _validator(media_tools, artifact_storage, validation_policy).validate(
        scene
    )

    assert _check(report, "media.audio_required").status == CheckStatus.FAIL
    assert report.is_blocked


def test_deterministic_text_mismatch_blocks(
    artifact_storage: ArtifactStorageService,
    media_tools: FFmpegTools,
    validation_policy: ValidationPolicy,
    tmp_path: Path,
) -> None:
    scene = _generated_scene(
        artifact_storage,
        media_tools,
        tmp_path,
        manifest_texts=["Not the authoritative text"],
    )
    report = _validator(media_tools, artifact_storage, validation_policy).validate(
        scene
    )

    assert _check(report, "text.accuracy").status == CheckStatus.FAIL
    assert report.is_blocked


def test_style_conflict_blocks(
    artifact_storage: ArtifactStorageService,
    media_tools: FFmpegTools,
    validation_policy: ValidationPolicy,
    tmp_path: Path,
) -> None:
    scene = _generated_scene(artifact_storage, media_tools, tmp_path)
    report = _validator(media_tools, artifact_storage, validation_policy).validate(
        scene,
        expected_style="different_style",
    )

    assert _check(report, "style.expected").status == CheckStatus.FAIL
    assert report.is_blocked


def test_scene_without_media_cannot_pass(
    artifact_storage: ArtifactStorageService,
    media_tools: FFmpegTools,
    validation_policy: ValidationPolicy,
) -> None:
    report = _validator(media_tools, artifact_storage, validation_policy).validate(
        make_scene()
    )

    assert _check(report, "media.video_required").status == CheckStatus.FAIL
    assert _check(report, "contract.generation_completed").status == CheckStatus.FAIL
    assert report.is_blocked


def test_validation_agent_persists_report_and_rejects(
    artifact_storage: ArtifactStorageService,
    media_tools: FFmpegTools,
    validation_policy: ValidationPolicy,
    tmp_path: Path,
) -> None:
    scene = _generated_scene(
        artifact_storage,
        media_tools,
        tmp_path,
        target_duration=5.0,
    )
    agent = SceneValidationAgent(
        _validator(media_tools, artifact_storage, validation_policy),
        artifact_storage,
    )

    updated = agent.validate_scene(
        scene,
        job_id="job_validation",
        expected_style="clean_whiteboard",
        scene_ids=[scene.scene_id],
        total_scenes=1,
    )

    assert updated.status == SceneStatus.REJECTED
    assert updated.validation is not None
    assert updated.validation.is_blocked
    assert updated.failure is not None
    assert updated.failure.check_id == "duration.target_delta"
    prefix = artifact_storage.scene_prefix(
        "job_validation", scene.scene_index, scene.scene_id
    )
    assert artifact_storage.exists(f"{prefix}/validation.json")
    assert artifact_storage.exists(f"{prefix}/scene.json")
