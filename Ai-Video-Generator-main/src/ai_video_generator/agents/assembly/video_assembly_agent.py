"""Thin assembly agent over :class:`VideoAssemblyService`."""

from __future__ import annotations

from typing import Callable

from ai_video_generator.services.video_assembly_service import (
    AssemblyResult,
    ProgressCallback,
    VideoAssemblyService,
)
from ai_video_generator.video.scene_contract import SceneSet


class VideoAssemblyAgent:
    def __init__(self, service: VideoAssemblyService) -> None:
        self.service = service

    def assemble(
        self,
        scene_set: SceneSet,
        *,
        job_id: str,
        progress: ProgressCallback | None = None,
        cancellation_check: Callable[[], bool] | None = None,
    ) -> AssemblyResult:
        return self.service.assemble(
            scene_set,
            job_id=job_id,
            progress=progress,
            cancellation_check=cancellation_check,
        )


__all__ = ["VideoAssemblyAgent"]
