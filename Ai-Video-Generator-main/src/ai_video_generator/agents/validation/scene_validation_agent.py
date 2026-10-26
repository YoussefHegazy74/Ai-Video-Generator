"""Scene validation agent.

Validation is a gate, not a score. Blocking failures move the scene to
``rejected`` and prevent assembly; warnings do not block unless the configured
policy says they do. Reports are persisted as JSON artifacts and the scene record
is updated for downstream orchestration.
"""

from __future__ import annotations

from ai_video_generator.core.constants import ArtifactKind, SceneStatus
from ai_video_generator.core.exceptions import (
    AppError,
    DuplicateGenerationError,
    SceneStateError,
)
from ai_video_generator.core.logging import get_logger
from ai_video_generator.services.storage_service import ArtifactStorageService
from ai_video_generator.video.audio_video_validation import SceneValidator
from ai_video_generator.video.scene_contract import (
    FailureDetail,
    SceneSpec,
    ValidationReport,
)

logger = get_logger(__name__)


class SceneValidationAgent:
    def __init__(
        self,
        validator: SceneValidator,
        storage: ArtifactStorageService,
    ) -> None:
        self.validator = validator
        self.storage = storage

    def validate_scene(
        self,
        scene: SceneSpec,
        *,
        job_id: str,
        expected_style: str | None = None,
        scene_ids: list[str] | None = None,
        total_scenes: int | None = None,
    ) -> SceneSpec:
        if scene.status == SceneStatus.VALIDATING:
            raise DuplicateGenerationError(
                f"Scene {scene.scene_id!r} is already being validated",
                details={"scene_id": scene.scene_id},
            )
        if scene.status not in {SceneStatus.GENERATED, SceneStatus.VALIDATED}:
            raise SceneStateError(
                f"Scene {scene.scene_id!r} cannot be validated from status "
                f"{scene.status.value!r}",
                details={"scene_id": scene.scene_id, "status": scene.status.value},
            )

        scene.transition_to(SceneStatus.VALIDATING)
        try:
            report = self.validator.validate(
                scene,
                expected_style=expected_style,
                scene_ids=scene_ids,
                total_scenes=total_scenes,
            )
            self._persist_report(report, scene, job_id)
            scene.validation = report
            if report.is_blocked:
                first = report.blocking_failures[0]
                scene.failure = FailureDetail(
                    code="validation_blocked",
                    message=report.human_summary(),
                    stage="validation",
                    retryable=report.retry_recommended,
                    check_id=first.check_id,
                    details={
                        "blocking_checks": [
                            {
                                "check_id": check.check_id,
                                "category": check.category,
                                "message": check.message,
                                "retryable": check.retryable,
                            }
                            for check in report.blocking_failures
                        ]
                    },
                )
                scene.transition_to(SceneStatus.REJECTED)
                logger.warning(
                    "Scene validation blocked assembly",
                    extra={
                        "job_id": job_id,
                        "scene_id": scene.scene_id,
                        "blocking_failures": report.blocking_failure_count,
                        "retry_recommended": report.retry_recommended,
                    },
                )
            else:
                scene.failure = None
                scene.transition_to(SceneStatus.VALIDATED)
                logger.info(
                    "Scene validated",
                    extra={
                        "job_id": job_id,
                        "scene_id": scene.scene_id,
                        "warnings": report.warning_count,
                        "unavailable": report.unavailable_count,
                    },
                )
        except AppError as exc:
            scene.failure = FailureDetail(
                code=exc.code,
                message=exc.message,
                stage="validation",
                retryable=exc.retryable,
                details=exc.details,
            )
            if scene.status == SceneStatus.VALIDATING:
                scene.transition_to(SceneStatus.FAILED)
            self._persist_scene_record(scene, job_id)
            raise
        except Exception as exc:  # pragma: no cover - defensive boundary
            scene.failure = FailureDetail(
                code="validation_unexpected_error",
                message=f"Unexpected {type(exc).__name__} during validation",
                stage="validation",
                retryable=False,
            )
            if scene.status == SceneStatus.VALIDATING:
                scene.transition_to(SceneStatus.FAILED)
            self._persist_scene_record(scene, job_id)
            raise

        self._persist_scene_record(scene, job_id)
        return scene

    def _persist_report(
        self, report: ValidationReport, scene: SceneSpec, job_id: str
    ) -> None:
        prefix = self.storage.scene_prefix(job_id, scene.scene_index, scene.scene_id)
        self.storage.store_json(
            report.model_dump(mode="json"),
            f"{prefix}/validation.json",
            ArtifactKind.VALIDATION_REPORT,
            job_id=job_id,
            scene_id=scene.scene_id,
            metadata={
                "blocked": report.is_blocked,
                "blocking_failures": report.blocking_failure_count,
                "warnings": report.warning_count,
            },
        )

    def _persist_scene_record(self, scene: SceneSpec, job_id: str) -> None:
        prefix = self.storage.scene_prefix(job_id, scene.scene_index, scene.scene_id)
        self.storage.store_json(
            scene.model_dump(mode="json"),
            f"{prefix}/scene.json",
            ArtifactKind.SCENE_RECORD,
            job_id=job_id,
            scene_id=scene.scene_id,
            metadata={"status": scene.status.value},
        )


__all__ = ["SceneValidationAgent"]
