"""Thin generation agent over :class:`SceneGenerationService`."""

from __future__ import annotations

from typing import Callable

from ai_video_generator.services.scene_generation_service import (
    SceneGenerationService,
)
from ai_video_generator.video.scene_contract import SceneSpec


class VideoGenerationAgent:
    def __init__(self, service: SceneGenerationService) -> None:
        self.service = service

    def generate_scene(
        self,
        scene: SceneSpec,
        *,
        job_id: str,
        scene_count: int,
        cancellation_check: Callable[[], bool] | None = None,
    ) -> SceneSpec:
        return self.service.generate_scene(
            scene,
            job_id=job_id,
            scene_count=scene_count,
            cancellation_check=cancellation_check,
        )


__all__ = ["VideoGenerationAgent"]
