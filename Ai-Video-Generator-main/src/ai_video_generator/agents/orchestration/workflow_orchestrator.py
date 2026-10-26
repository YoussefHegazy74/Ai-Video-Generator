"""Synchronous generate -> validate -> assemble workflow.

There is no API or worker infrastructure in this repository. This orchestrator is
the integration boundary: it owns job state, persists job/scene manifests, checks
cancellation between work units, retries generation when validation explicitly
recommends it, and refuses assembly unless every scene is validated.
"""

from __future__ import annotations

from typing import Callable

from ai_video_generator.agents.validation.scene_validation_agent import (
    SceneValidationAgent,
)
from ai_video_generator.core.constants import ArtifactKind, JobStage, SceneStatus
from ai_video_generator.core.exceptions import (
    AppError,
    PipelineCancelledError,
)
from ai_video_generator.core.logging import get_logger
from ai_video_generator.models.generation_job import GenerationJob
from ai_video_generator.services.scene_generation_service import (
    SceneGenerationService,
)
from ai_video_generator.services.storage_service import ArtifactStorageService
from ai_video_generator.services.video_assembly_service import VideoAssemblyService
from ai_video_generator.utils.identifiers import new_id
from ai_video_generator.video.continuity import validate_scene_set_structure
from ai_video_generator.video.scene_contract import (
    FailureDetail,
    SceneSet,
)

logger = get_logger(__name__)

CancellationCheck = Callable[[], bool]
JobProgressCallback = Callable[[GenerationJob, str], None]


class WorkflowOrchestrator:
    def __init__(
        self,
        generation_service: SceneGenerationService,
        validation_agent: SceneValidationAgent,
        assembly_service: VideoAssemblyService,
        storage: ArtifactStorageService,
        *,
        max_validation_attempts: int = 2,
        progress_callback: JobProgressCallback | None = None,
        cancellation_check: CancellationCheck | None = None,
    ) -> None:
        if max_validation_attempts < 1:
            raise ValueError("max_validation_attempts must be at least 1")
        self.generation_service = generation_service
        self.validation_agent = validation_agent
        self.assembly_service = assembly_service
        self.storage = storage
        self.max_validation_attempts = max_validation_attempts
        self.progress_callback = progress_callback
        self.cancellation_check = cancellation_check or (lambda: False)

    def run(
        self,
        scene_set: SceneSet,
        *,
        job_id: str | None = None,
    ) -> GenerationJob:
        job = GenerationJob.for_scene_set(job_id or new_id("job"), scene_set)
        try:
            return self._run(job, scene_set)
        except Exception as exc:
            if not job.is_terminal:
                failure = FailureDetail(
                    code=getattr(exc, "code", "pipeline_unexpected_error"),
                    message=getattr(
                        exc,
                        "message",
                        f"Unexpected {type(exc).__name__} in the pipeline",
                    ),
                    stage="pipeline",
                    retryable=bool(getattr(exc, "retryable", False)),
                    details=getattr(exc, "details", {}),
                )
                try:
                    job.fail(failure, progress_percent=job.progress_percent)
                    self._persist(job, scene_set)
                except Exception:  # pragma: no cover - persistence failure path
                    logger.exception(
                        "Failed to persist unexpected pipeline failure",
                        extra={"job_id": job.job_id},
                    )
            raise

    # ------------------------------------------------------------------ pipeline

    def _run(self, job: GenerationJob, scene_set: SceneSet) -> GenerationJob:
        job.start()
        job.metadata["scene_set_key"] = (
            f"{self.storage.job_prefix(job.job_id)}/scene_set.json"
        )
        job.metadata["job_manifest_key"] = (
            f"{self.storage.job_prefix(job.job_id)}/job.json"
        )
        self._persist(job, scene_set)
        self._notify(job, "Job started")

        continuity = validate_scene_set_structure(scene_set)
        if continuity.is_blocked:
            failure = FailureDetail(
                code="scene_set_continuity_failed",
                message=continuity.human_summary(),
                stage="preparation",
                retryable=False,
                details={
                    "blocking_checks": [
                        check.model_dump(mode="json")
                        for check in continuity.blocking_failures
                    ]
                },
            )
            job.fail(failure, progress_percent=job.progress_percent)
            self._persist(job, scene_set)
            self._notify(job, "Scene-set continuity failed")
            return job

        ordered = scene_set.ordered_scenes
        total = len(ordered)
        scene_ids = [scene.scene_id for scene in ordered]
        generated_ids: set[str] = set()
        validated_ids: set[str] = set()

        for position, original in enumerate(ordered):
            if self.cancellation_check():
                job.cancel("Cancelled before scene generation")
                self._persist(job, scene_set)
                self._notify(job, "Cancelled")
                return job

            scene = scene_set.scenes[scene_set.scenes.index(original)]
            validation_attempts = 0
            while True:
                validation_attempts += 1
                generation_progress = 5 + int(40 * (position / total))
                self._set_stage(
                    job,
                    JobStage.GENERATING,
                    f"Generating scene {scene.scene_id}",
                    generation_progress,
                    scene_id=scene.scene_id,
                )
                try:
                    scene = self.generation_service.generate_scene(
                        scene,
                        job_id=job.job_id,
                        scene_count=total,
                        cancellation_check=self.cancellation_check,
                    )
                except PipelineCancelledError:
                    job.cancel("Cancelled during scene generation")
                    self._persist(job, scene_set)
                    self._notify(job, "Cancelled")
                    return job
                except AppError as exc:
                    scene_set.replace_scene(scene)
                    self._fail(
                        job,
                        scene_set,
                        code=exc.code,
                        message=exc.message,
                        stage="generation",
                        retryable=exc.retryable,
                        details=exc.details,
                        scene_id=scene.scene_id,
                    )
                    return job

                scene_set.replace_scene(scene)
                if scene.scene_id not in generated_ids:
                    job.mark_scene_generated(scene.scene_id, job.progress_percent)
                    generated_ids.add(scene.scene_id)
                self._persist(job, scene_set)
                self._notify(job, f"Generated scene {scene.scene_id}")

                if self.cancellation_check():
                    job.cancel("Cancelled before scene validation")
                    self._persist(job, scene_set)
                    self._notify(job, "Cancelled")
                    return job

                validation_progress = 45 + int(
                    25 * ((position + 1) / total)
                )
                self._set_stage(
                    job,
                    JobStage.VALIDATING,
                    f"Validating scene {scene.scene_id}",
                    validation_progress,
                    scene_id=scene.scene_id,
                )
                try:
                    scene = self.validation_agent.validate_scene(
                        scene,
                        job_id=job.job_id,
                        expected_style=scene_set.expected_style,
                        scene_ids=scene_ids,
                        total_scenes=total,
                    )
                except AppError as exc:
                    scene_set.replace_scene(scene)
                    self._fail(
                        job,
                        scene_set,
                        code=exc.code,
                        message=exc.message,
                        stage="validation",
                        retryable=exc.retryable,
                        details=exc.details,
                        scene_id=scene.scene_id,
                    )
                    return job

                scene_set.replace_scene(scene)
                self._persist(job, scene_set)
                self._notify(job, f"Validated scene {scene.scene_id}")

                if scene.status == SceneStatus.VALIDATED:
                    if scene.scene_id not in validated_ids:
                        job.mark_scene_validated(
                            scene.scene_id, job.progress_percent
                        )
                        validated_ids.add(scene.scene_id)
                    self._persist(job, scene_set)
                    break

                if scene.status == SceneStatus.REJECTED:
                    report = scene.validation
                    retry_recommended = bool(
                        report is not None and report.retry_recommended
                    )
                    if (
                        retry_recommended
                        and validation_attempts < self.max_validation_attempts
                    ):
                        logger.warning(
                            "Scene rejected with retryable failures; regenerating",
                            extra={
                                "job_id": job.job_id,
                                "scene_id": scene.scene_id,
                                "validation_attempt": validation_attempts,
                                "max_validation_attempts": self.max_validation_attempts,
                            },
                        )
                        self._notify(
                            job,
                            f"Scene {scene.scene_id} rejected; retrying generation",
                        )
                        continue
                    first_check = (
                        report.blocking_failures[0].check_id
                        if report is not None and report.blocking_failures
                        else None
                    )
                    self._fail(
                        job,
                        scene_set,
                        code="validation_blocked",
                        message=(
                            report.human_summary()
                            if report is not None
                            else "Scene validation was rejected"
                        ),
                        stage="validation",
                        retryable=retry_recommended,
                        details={
                            "scene_id": scene.scene_id,
                            "check_id": first_check,
                            "validation_attempts": validation_attempts,
                        },
                        scene_id=scene.scene_id,
                    )
                    return job

                self._fail(
                    job,
                    scene_set,
                    code="unexpected_scene_state",
                    message=(
                        f"Scene {scene.scene_id!r} ended validation in unexpected "
                        f"state {scene.status.value!r}"
                    ),
                    stage="validation",
                    retryable=False,
                    details={"scene_id": scene.scene_id},
                    scene_id=scene.scene_id,
                )
                return job

        if self.cancellation_check():
            job.cancel("Cancelled before assembly")
            self._persist(job, scene_set)
            self._notify(job, "Cancelled")
            return job

        self._set_stage(
            job,
            JobStage.ASSEMBLING,
            "Assembling final video",
            78,
        )

        def assembly_progress(stage: str, fraction: float) -> None:
            bounded = max(0.0, min(1.0, fraction))
            overall = 78 + int(17 * bounded)
            target_stage = (
                JobStage.VERIFYING if stage == "verifying_output" else JobStage.ASSEMBLING
            )
            self._set_stage(
                job,
                target_stage,
                f"Assembly: {stage}",
                overall,
            )

        try:
            result = self.assembly_service.assemble(
                scene_set,
                job_id=job.job_id,
                progress=assembly_progress,
                cancellation_check=self.cancellation_check,
            )
        except PipelineCancelledError:
            job.cancel("Cancelled during assembly")
            self._persist(job, scene_set)
            self._notify(job, "Cancelled")
            return job
        except AppError as exc:
            self._fail(
                job,
                scene_set,
                code=exc.code,
                message=exc.message,
                stage="assembly",
                retryable=exc.retryable,
                details=exc.details,
            )
            return job

        job.metadata["assembly_manifest_key"] = result.manifest_key
        job.metadata["output_sha256"] = result.manifest.output_sha256
        job.complete(result.final_video_key, result.manifest_key)
        self._persist(job, scene_set)
        self._notify(job, "Final video verified")
        return job

    # ------------------------------------------------------------------ helpers

    def _set_stage(
        self,
        job: GenerationJob,
        stage: JobStage,
        message: str,
        progress_percent: int,
        *,
        scene_id: str | None = None,
    ) -> None:
        bounded = max(job.progress_percent, min(100, int(progress_percent)))
        job.set_stage(
            stage,
            message,
            bounded,
            current_scene_id=scene_id,
        )

    def _fail(
        self,
        job: GenerationJob,
        scene_set: SceneSet,
        *,
        code: str,
        message: str,
        stage: str,
        retryable: bool,
        details: dict[str, object] | None,
        scene_id: str | None = None,
    ) -> None:
        raw_check_id = (details or {}).get("check_id")
        failure = FailureDetail(
            code=code,
            message=message,
            stage=stage,
            retryable=retryable,
            check_id=raw_check_id if isinstance(raw_check_id, str) else None,
            details=details or {},
        )
        job.fail(failure, progress_percent=job.progress_percent)
        self._persist(job, scene_set)
        self._notify(job, f"Job failed: {code}")
        logger.error(
            "Pipeline failed",
            extra={
                "job_id": job.job_id,
                "scene_id": scene_id,
                "stage": stage,
                "error_code": code,
                "retryable": retryable,
            },
        )

    def _persist(self, job: GenerationJob, scene_set: SceneSet) -> None:
        prefix = self.storage.job_prefix(job.job_id)
        self.storage.store_json(
            scene_set.model_dump(mode="json"),
            f"{prefix}/scene_set.json",
            ArtifactKind.JOB_MANIFEST,
            job_id=job.job_id,
            project_id=scene_set.project_id,
            metadata={"job_status": job.status.value},
        )
        self.storage.store_json(
            job.model_dump(mode="json"),
            f"{prefix}/job.json",
            ArtifactKind.JOB_MANIFEST,
            job_id=job.job_id,
            project_id=scene_set.project_id,
            metadata={"job_status": job.status.value, "stage": job.stage.value},
        )

    def _notify(self, job: GenerationJob, message: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(job, message)


__all__ = [
    "CancellationCheck",
    "JobProgressCallback",
    "WorkflowOrchestrator",
]
