from __future__ import annotations

import random

import pytest

from ai_video_generator.core.constants import SceneStatus
from ai_video_generator.core.exceptions import (
    DuplicateGenerationError,
    PipelineCancelledError,
    ProviderError,
    SceneGenerationError,
)
from ai_video_generator.services.scene_generation_service import (
    SceneGenerationService,
)
from ai_video_generator.services.storage_service import ArtifactStorageService
from ai_video_generator.video.media_probe import FFmpegTools

from tests.helpers import FakeProvider, make_scene


def _service(
    provider: FakeProvider,
    artifact_storage: ArtifactStorageService,
    media_tools: FFmpegTools,
    *,
    max_attempts: int = 3,
    delays: list[float] | None = None,
) -> SceneGenerationService:
    recorded = delays if delays is not None else []
    return SceneGenerationService(
        provider,
        artifact_storage,
        media_tools,
        max_attempts=max_attempts,
        timeout_seconds=30.0,
        backoff_base_seconds=0.01,
        backoff_max_seconds=0.02,
        backoff_jitter_seconds=0.0,
        sync_tolerance_seconds=0.35,
        sleep=recorded.append,
        random_source=random.Random(0),
    )


def test_generation_stores_measured_metadata_not_requested_duration(
    artifact_storage: ArtifactStorageService,
    media_tools: FFmpegTools,
) -> None:
    provider = FakeProvider(media_tools, clip_duration=1.0)
    service = _service(provider, artifact_storage, media_tools)
    scene = make_scene("gen-1", target_duration=5.0)

    result = service.generate_scene(scene, job_id="job_gen", scene_count=1)

    assert result.status == SceneStatus.GENERATED
    assert result.generation is not None
    assert result.generation.requested_duration_seconds == 5.0
    assert result.generation.selected_duration_seconds == 5.0
    assert result.generation.duration_control is True
    assert result.media.video is not None
    assert result.media.video.duration_seconds is not None
    assert result.media.video.duration_seconds == pytest.approx(1.0, abs=0.15)
    assert result.media.video.duration_seconds != result.target_duration_seconds
    assert result.media.audio_embedded is True
    assert result.media.manifest is not None
    assert result.retry.attempts == 1
    assert result.generation.input_fingerprint
    assert len(result.generation.input_fingerprint) == 64

    prefix = artifact_storage.scene_prefix("job_gen", scene.scene_index, scene.scene_id)
    assert artifact_storage.exists(f"{prefix}/video.mp4")
    assert artifact_storage.exists(f"{prefix}/manifest.json")
    assert artifact_storage.exists(f"{prefix}/scene.json")


def test_generation_retries_retryable_failures_with_bounded_backoff(
    artifact_storage: ArtifactStorageService,
    media_tools: FFmpegTools,
) -> None:
    provider = FakeProvider(
        media_tools,
        clip_duration=1.0,
        failure_plan={
            1: ProviderError(
                "temporary upstream failure",
                provider="fake_provider",
                retryable=True,
            ),
            2: ProviderError(
                "temporary upstream failure",
                provider="fake_provider",
                retryable=True,
            ),
        },
    )
    delays: list[float] = []
    service = _service(
        provider,
        artifact_storage,
        media_tools,
        max_attempts=3,
        delays=delays,
    )

    result = service.generate_scene(
        make_scene("gen-retry", target_duration=1.0),
        job_id="job_retry",
        scene_count=1,
    )

    assert len(provider.requests) == 3
    assert delays == pytest.approx([0.01, 0.02])
    assert result.retry.attempts == 3
    assert result.retry.last_error_code == "provider_error"
    assert result.retry.retryable is False
    assert result.status == SceneStatus.GENERATED


def test_generation_does_not_retry_non_retryable_failures(
    artifact_storage: ArtifactStorageService,
    media_tools: FFmpegTools,
) -> None:
    provider = FakeProvider(
        media_tools,
        failure_plan={
            1: ProviderError(
                "invalid request",
                provider="fake_provider",
                retryable=False,
            )
        },
    )
    delays: list[float] = []
    service = _service(
        provider,
        artifact_storage,
        media_tools,
        max_attempts=3,
        delays=delays,
    )
    scene = make_scene("gen-fail", target_duration=1.0)

    with pytest.raises(SceneGenerationError) as error:
        service.generate_scene(scene, job_id="job_fail", scene_count=1)

    assert error.value.attempts == 1
    assert len(provider.requests) == 1
    assert delays == []
    assert scene.status == SceneStatus.FAILED
    assert scene.failure is not None
    assert scene.failure.code == "provider_error"
    assert scene.failure.stage == "generation"


def test_generation_refuses_duplicate_when_lock_is_held(
    artifact_storage: ArtifactStorageService,
    media_tools: FFmpegTools,
) -> None:
    provider = FakeProvider(media_tools)
    service = _service(provider, artifact_storage, media_tools)
    scene = make_scene("gen-lock", target_duration=1.0)
    lock_key = (
        f"{artifact_storage.job_prefix('job_lock')}/locks/000-gen-lock.lock"
    )
    artifact_storage.storage.put_bytes(b"held-by-another-process", lock_key)

    with pytest.raises(DuplicateGenerationError):
        service.generate_scene(scene, job_id="job_lock", scene_count=1)

    assert provider.requests == []
    assert scene.status == SceneStatus.PENDING


def test_generation_reuses_existing_generated_scene(
    artifact_storage: ArtifactStorageService,
    media_tools: FFmpegTools,
) -> None:
    provider = FakeProvider(media_tools)
    service = _service(provider, artifact_storage, media_tools)
    scene = make_scene("gen-reuse", target_duration=1.0)

    first = service.generate_scene(scene, job_id="job_reuse", scene_count=1)
    second = service.generate_scene(scene, job_id="job_reuse", scene_count=1)

    assert len(provider.requests) == 1
    assert second is first
    assert second.status == SceneStatus.GENERATED


def test_generation_cancels_before_mutating_scene(
    artifact_storage: ArtifactStorageService,
    media_tools: FFmpegTools,
) -> None:
    provider = FakeProvider(media_tools)
    service = _service(provider, artifact_storage, media_tools)
    scene = make_scene("gen-cancel", target_duration=1.0)

    with pytest.raises(PipelineCancelledError):
        service.generate_scene(
            scene,
            job_id="job_cancel",
            scene_count=1,
            cancellation_check=lambda: True,
        )

    assert provider.requests == []
    assert scene.status == SceneStatus.PENDING
