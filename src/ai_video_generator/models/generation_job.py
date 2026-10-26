"""Generation job lifecycle and progress tracking.

Progress percentages are supplied by the orchestrator from completed work units
(scenes generated/validated, assembly stages completed), not fabricated by this
model. Terminal states are explicit and final.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_video_generator.core.constants import JobStage, JobStatus
from ai_video_generator.core.exceptions import JobStateError
from ai_video_generator.video.scene_contract import (
    FailureDetail,
    SceneSet,
    utc_now,
)


class JobStageEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: JobStage
    message: str
    progress_percent: int = Field(ge=0, le=100)
    at: datetime = Field(default_factory=utc_now)


class GenerationJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    project_id: str | None = None
    course_title: str | None = None
    scene_ids: list[str] = Field(min_length=1)
    total_scenes: int = Field(ge=1)
    completed_scenes: int = Field(default=0, ge=0)
    validated_scenes: int = Field(default=0, ge=0)
    status: JobStatus = JobStatus.PENDING
    stage: JobStage = JobStage.PENDING
    progress_percent: int = Field(default=0, ge=0, le=100)
    current_scene_id: str | None = None
    final_video_key: str | None = None
    assembly_manifest_key: str | None = None
    failures: list[FailureDetail] = Field(default_factory=list)
    stage_history: list[JobStageEvent] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("job_id")
    @classmethod
    def _valid_job_id(cls, value: str) -> str:
        import re

        if not re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", value):
            raise ValueError("job_id must be a safe identifier")
        return value

    @classmethod
    def for_scene_set(cls, job_id: str, scene_set: SceneSet) -> "GenerationJob":
        scenes = scene_set.ordered_scenes
        return cls(
            job_id=job_id,
            project_id=scene_set.project_id,
            course_title=scene_set.course_title,
            scene_ids=[scene.scene_id for scene in scenes],
            total_scenes=len(scenes),
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }

    def _require_not_terminal(self, action: str) -> None:
        if self.is_terminal:
            raise JobStateError(
                f"Cannot {action} job {self.job_id} in terminal state "
                f"{self.status.value!r}",
                details={"job_id": self.job_id, "status": self.status.value},
            )

    def start(self) -> None:
        self._require_not_terminal("start")
        if self.status == JobStatus.PENDING:
            self.status = JobStatus.RUNNING
            self.started_at = utc_now()
        self.set_stage(JobStage.PREPARING, "Preparing scenes and configuration", 0)

    def set_stage(
        self,
        stage: JobStage,
        message: str,
        progress_percent: int | None = None,
        *,
        current_scene_id: str | None = None,
    ) -> None:
        self._require_not_terminal(f"advance to {stage.value}")
        if self.status == JobStatus.PENDING:
            self.status = JobStatus.RUNNING
            self.started_at = self.started_at or utc_now()
        if progress_percent is not None:
            if progress_percent < self.progress_percent:
                raise JobStateError(
                    "Job progress must not decrease",
                    details={
                        "job_id": self.job_id,
                        "current": self.progress_percent,
                        "requested": progress_percent,
                    },
                )
            self.progress_percent = progress_percent
        self.stage = stage
        self.current_scene_id = current_scene_id
        self.stage_history.append(
            JobStageEvent(
                stage=stage,
                message=message,
                progress_percent=self.progress_percent,
            )
        )
        self.updated_at = utc_now()

    def mark_scene_generated(self, scene_id: str, progress_percent: int) -> None:
        if scene_id not in self.scene_ids:
            raise JobStateError(
                f"Scene {scene_id!r} is not part of job {self.job_id}",
                details={"job_id": self.job_id, "scene_id": scene_id},
            )
        self.completed_scenes += 1
        self.current_scene_id = scene_id
        self._set_progress(progress_percent)
        self.updated_at = utc_now()

    def mark_scene_validated(self, scene_id: str, progress_percent: int) -> None:
        if scene_id not in self.scene_ids:
            raise JobStateError(
                f"Scene {scene_id!r} is not part of job {self.job_id}",
                details={"job_id": self.job_id, "scene_id": scene_id},
            )
        self.validated_scenes += 1
        self.current_scene_id = scene_id
        self._set_progress(progress_percent)
        self.updated_at = utc_now()

    def _set_progress(self, progress_percent: int) -> None:
        if not 0 <= progress_percent <= 100:
            raise JobStateError(
                f"Progress must be between 0 and 100, got {progress_percent}",
                details={"job_id": self.job_id},
            )
        if progress_percent < self.progress_percent:
            raise JobStateError(
                "Job progress must not decrease",
                details={
                    "job_id": self.job_id,
                    "current": self.progress_percent,
                    "requested": progress_percent,
                },
            )
        self.progress_percent = progress_percent

    def complete(self, final_video_key: str, assembly_manifest_key: str) -> None:
        self._require_not_terminal("complete")
        if self.validated_scenes != self.total_scenes:
            raise JobStateError(
                "Cannot complete a job before every scene is validated",
                details={
                    "job_id": self.job_id,
                    "validated": self.validated_scenes,
                    "total": self.total_scenes,
                },
            )
        if not final_video_key or not assembly_manifest_key:
            raise JobStateError(
                "Cannot complete a job without verified final artifacts",
                details={"job_id": self.job_id},
            )
        self.status = JobStatus.COMPLETED
        self.stage = JobStage.COMPLETED
        self.final_video_key = final_video_key
        self.assembly_manifest_key = assembly_manifest_key
        self.progress_percent = 100
        self.current_scene_id = None
        self.finished_at = utc_now()
        self.updated_at = self.finished_at
        self.stage_history.append(
            JobStageEvent(
                stage=JobStage.COMPLETED,
                message="Final video verified",
                progress_percent=100,
            )
        )

    def fail(self, failure: FailureDetail, *, progress_percent: int | None = None) -> None:
        if self.is_terminal and self.status != JobStatus.FAILED:
            raise JobStateError(
                f"Cannot fail job {self.job_id} in terminal state "
                f"{self.status.value!r}",
                details={"job_id": self.job_id, "status": self.status.value},
            )
        if progress_percent is not None:
            self._set_progress(progress_percent)
        self.failures.append(failure)
        self.status = JobStatus.FAILED
        self.stage = JobStage.FAILED
        self.current_scene_id = None
        self.finished_at = utc_now()
        self.updated_at = self.finished_at
        self.stage_history.append(
            JobStageEvent(
                stage=JobStage.FAILED,
                message=failure.message,
                progress_percent=self.progress_percent,
            )
        )

    def cancel(self, message: str = "Cancelled by caller") -> None:
        self._require_not_terminal("cancel")
        self.status = JobStatus.CANCELLED
        self.stage = JobStage.CANCELLED
        self.current_scene_id = None
        self.finished_at = utc_now()
        self.updated_at = self.finished_at
        self.stage_history.append(
            JobStageEvent(
                stage=JobStage.CANCELLED,
                message=message,
                progress_percent=self.progress_percent,
            )
        )
