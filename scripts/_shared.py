"""Shared helpers for the runnable pipeline scripts.

These helpers keep CLI argument-to-settings overrides identical across the
runtime-only and full lesson entry points. They are script support code, not part
of the installed application package.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def resolve_input_path(path_value: str) -> Path:
    """Resolve a user-supplied path against the repository root when relative."""
    path = Path(path_value).expanduser()
    if not path.is_absolute() and not path.exists():
        path = ROOT / path
    return path.resolve()


def apply_overrides(settings: Any, args: Any) -> Any:
    """Apply media/output CLI overrides to a validated settings object."""
    updates: dict[str, Any] = {}
    provider = settings.provider
    if getattr(args, "provider", None) is not None:
        provider = provider.model_copy(update={"id": args.provider})
    whiteboard = provider.whiteboard
    if getattr(args, "width", None) is not None:
        whiteboard = whiteboard.model_copy(update={"width": args.width})
    if getattr(args, "height", None) is not None:
        whiteboard = whiteboard.model_copy(update={"height": args.height})
    if getattr(args, "fps", None) is not None:
        whiteboard = whiteboard.model_copy(update={"fps": args.fps})
    provider = provider.model_copy(update={"whiteboard": whiteboard})
    updates["provider"] = provider

    assembly = settings.assembly
    assembly_updates: dict[str, Any] = {}
    if getattr(args, "width", None) is not None:
        assembly_updates["width"] = args.width
    if getattr(args, "height", None) is not None:
        assembly_updates["height"] = args.height
    if getattr(args, "fps", None) is not None:
        assembly_updates["fps"] = args.fps
    if getattr(args, "no_intro", False):
        assembly_updates["include_intro"] = False
    if getattr(args, "no_outro", False):
        assembly_updates["include_outro"] = False
    if assembly_updates:
        assembly = assembly.model_copy(update=assembly_updates)
    updates["assembly"] = assembly

    if getattr(args, "log_level", None) is not None:
        updates["log_level"] = args.log_level.upper()
    return settings.model_copy(update=updates)


def build_summary(job: Any, scene_set: Any, storage: Any) -> dict[str, Any]:
    """Build the machine-readable run summary printed by both CLIs."""
    return {
        "job_id": job.job_id,
        "status": job.status.value,
        "stage": job.stage.value,
        "progress_percent": job.progress_percent,
        "total_scenes": job.total_scenes,
        "validated_scenes": job.validated_scenes,
        "final_video": (
            str(storage.absolute_path(job.final_video_key))
            if job.final_video_key
            else None
        ),
        "assembly_manifest": (
            str(storage.absolute_path(job.assembly_manifest_key))
            if job.assembly_manifest_key
            else None
        ),
        "scenes": [
            {
                "scene_id": scene.scene_id,
                "status": scene.status.value,
                "measured_duration_seconds": (
                    scene.media.video.duration_seconds
                    if scene.media.video is not None
                    else None
                ),
                "blocking_validation_failures": (
                    scene.validation.blocking_failure_count
                    if scene.validation is not None
                    else None
                ),
            }
            for scene in scene_set.ordered_scenes
        ],
        "failures": [
            failure.model_dump(mode="json") for failure in job.failures
        ],
    }


__all__ = [
    "ROOT",
    "apply_overrides",
    "build_summary",
    "resolve_input_path",
]
