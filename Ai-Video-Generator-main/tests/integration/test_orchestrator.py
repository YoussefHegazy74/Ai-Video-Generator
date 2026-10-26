from __future__ import annotations

import json
from pathlib import Path

from ai_video_generator.agents.orchestration.workflow_orchestrator import (
    WorkflowOrchestrator,
)
from ai_video_generator.agents.validation.scene_validation_agent import (
    SceneValidationAgent,
)
from ai_video_generator.core.constants import JobStatus, SceneStatus, TransitionStyle
from ai_video_generator.services.scene_generation_service import (
    SceneGenerationService,
)
from ai_video_generator.services.storage_service import ArtifactStorageService
from ai_video_generator.services.video_assembly_service import VideoAssemblyService
from ai_video_generator.video.assembly import AssemblyOptions, FFmpegAssembler
from ai_video_generator.video.audio_video_validation import (
    SceneValidator,
    ValidationPolicy,
)
from ai_video_generator.video.media_probe import FFmpegTools

from tests.helpers import FakeProvider, make_scene, make_scene_set


def _options() -> AssemblyOptions:
    return AssemblyOptions(
        include_intro=False,
        include_outro=False,
        transition=TransitionStyle.CUT,
        width=320,
        height=180,
        fps=10,
        show_scene_titles=False,
        show_progress=False,
        preset="ultrafast",
        crf=28,
        duration_tolerance_seconds=1.5,
    )


def _orchestrator(
    provider: FakeProvider,
    artifact_storage: ArtifactStorageService,
    media_tools: FFmpegTools,
    policy: ValidationPolicy,
    *,
    max_validation_attempts: int = 2,
    cancellation_check=None,
) -> WorkflowOrchestrator:
    generation = SceneGenerationService(
        provider,
        artifact_storage,
        media_tools,
        max_attempts=2,
        timeout_seconds=30.0,
        backoff_base_seconds=0.01,
        backoff_max_seconds=0.02,
        backoff_jitter_seconds=0.0,
        sync_tolerance_seconds=0.35,
        sleep=lambda _: None,
    )
    validation = SceneValidationAgent(
        SceneValidator(media_tools, artifact_storage, policy=policy),
        artifact_storage,
    )
    assembly = VideoAssemblyService(
        FFmpegAssembler(media_tools, _options()),
        artifact_storage,
        media_tools,
        audio_silence_mean_db=-50.0,
        audio_silence_max_db=-45.0,
    )
    return WorkflowOrchestrator(
        generation,
        validation,
        assembly,
        artifact_storage,
        max_validation_attempts=max_validation_attempts,
        cancellation_check=cancellation_check,
    )


def test_orchestrator_completes_generate_validate_assemble(
    artifact_storage: ArtifactStorageService,
    media_tools: FFmpegTools,
    validation_policy: ValidationPolicy,
    tmp_path: Path,
) -> None:
    provider = FakeProvider(media_tools, clip_duration=1.0)
    orchestrator = _orchestrator(
        provider, artifact_storage, media_tools, validation_policy
    )
    scene_set = make_scene_set(
        [
            make_scene("one", scene_index=0, target_duration=1.0),
            make_scene("two", scene_index=1, target_duration=1.0),
        ]
    )

    job = orchestrator.run(scene_set, job_id="orch_success")

    assert job.status == JobStatus.COMPLETED
    assert job.progress_percent == 100
    assert job.validated_scenes == 2
    assert job.completed_scenes == 2
    assert job.final_video_key is not None
    assert job.assembly_manifest_key is not None
    assert artifact_storage.exists(job.final_video_key)
    assert artifact_storage.exists(job.assembly_manifest_key)
    assert [scene.status for scene in scene_set.ordered_scenes] == [
        SceneStatus.VALIDATED,
        SceneStatus.VALIDATED,
    ]

    manifest = artifact_storage.read_json(job.assembly_manifest_key)
    assert manifest["scene_order"] == ["one", "two"]
    assert manifest["verification"]["audio_not_silent"] is True

    prefix = artifact_storage.job_prefix("orch_success")
    persisted_job = json.loads(
        artifact_storage.storage.read_bytes(f"{prefix}/job.json")
    )
    assert persisted_job["status"] == "completed"
    assert artifact_storage.exists(f"{prefix}/scene_set.json")


def test_orchestrator_regenerates_after_retryable_validation_rejection(
    artifact_storage: ArtifactStorageService,
    media_tools: FFmpegTools,
    validation_policy: ValidationPolicy,
) -> None:
    provider = FakeProvider(
        media_tools,
        clip_duration=1.0,
        silent_attempts={1},
    )
    orchestrator = _orchestrator(
        provider,
        artifact_storage,
        media_tools,
        validation_policy,
        max_validation_attempts=2,
    )
    scene_set = make_scene_set(
        [make_scene("retry-scene", scene_index=0, target_duration=1.0)]
    )

    job = orchestrator.run(scene_set, job_id="orch_retry")

    assert job.status == JobStatus.COMPLETED
    assert len(provider.requests) == 2
    assert job.validated_scenes == 1
    assert scene_set.ordered_scenes[0].status == SceneStatus.VALIDATED


def test_orchestrator_fails_when_validation_cannot_be_satisfied(
    artifact_storage: ArtifactStorageService,
    media_tools: FFmpegTools,
    validation_policy: ValidationPolicy,
) -> None:
    provider = FakeProvider(
        media_tools,
        clip_duration=1.0,
        silent_attempts={1, 2},
    )
    orchestrator = _orchestrator(
        provider,
        artifact_storage,
        media_tools,
        validation_policy,
        max_validation_attempts=1,
    )
    scene_set = make_scene_set(
        [make_scene("bad-scene", scene_index=0, target_duration=1.0)]
    )

    job = orchestrator.run(scene_set, job_id="orch_reject")

    assert job.status == JobStatus.FAILED
    assert job.failures
    failure = job.failures[0]
    assert failure.code == "validation_blocked"
    assert failure.check_id == "sync.audio_not_silent"
    assert scene_set.ordered_scenes[0].status == SceneStatus.REJECTED
    assert job.final_video_key is None


def test_orchestrator_honors_cancellation_before_generation(
    artifact_storage: ArtifactStorageService,
    media_tools: FFmpegTools,
    validation_policy: ValidationPolicy,
) -> None:
    provider = FakeProvider(media_tools)
    orchestrator = _orchestrator(
        provider,
        artifact_storage,
        media_tools,
        validation_policy,
        cancellation_check=lambda: True,
    )
    scene_set = make_scene_set(
        [make_scene("cancelled-scene", scene_index=0, target_duration=1.0)]
    )

    job = orchestrator.run(scene_set, job_id="orch_cancel")

    assert job.status == JobStatus.CANCELLED
    assert provider.requests == []
    assert scene_set.ordered_scenes[0].status == SceneStatus.PENDING
