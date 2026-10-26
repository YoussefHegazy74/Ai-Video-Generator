#!/usr/bin/env python
"""Run the implemented generate -> validate -> assemble pipeline.

Example (Windows PowerShell):

    python scripts/run_pipeline.py --scene-set examples/scene_set.sample.json

The default provider is a deterministic local whiteboard renderer, not an AI
video model. It requires FFmpeg and a local TTS path (Windows SAPI or FFmpeg
flite). The optional Gemini Omni adapter requires GEMINI_API_KEY and makes paid
API calls; it is not used by default.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_video_generator.agents.orchestration.workflow_orchestrator import (  # noqa: E402
    WorkflowOrchestrator,
)
from ai_video_generator.agents.validation.scene_validation_agent import (  # noqa: E402
    SceneValidationAgent,
)
from ai_video_generator.core.exceptions import AppError  # noqa: E402
from ai_video_generator.core.logging import configure_logging, get_logger  # noqa: E402
from ai_video_generator.core.settings import load_settings  # noqa: E402
from ai_video_generator.integrations.provider_factory import build_provider  # noqa: E402
from ai_video_generator.models.generation_job import GenerationJob  # noqa: E402
from ai_video_generator.services.scene_generation_service import (  # noqa: E402
    SceneGenerationService,
)
from ai_video_generator.services.storage_service import (  # noqa: E402
    ArtifactStorageService,
)
from ai_video_generator.services.video_assembly_service import (  # noqa: E402
    VideoAssemblyService,
)
from ai_video_generator.storage.artifact_registry import ArtifactRegistry  # noqa: E402
from ai_video_generator.storage.file_storage import LocalFileStorage  # noqa: E402
from ai_video_generator.video.assembly import FFmpegAssembler  # noqa: E402
from ai_video_generator.video.audio_video_validation import SceneValidator  # noqa: E402
from ai_video_generator.video.media_probe import FFmpegTools  # noqa: E402
from ai_video_generator.video.scene_contract import SceneSet  # noqa: E402

logger = get_logger(__name__)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scene-set",
        default="examples/scene_set.sample.json",
        help="Scene-set JSON file (default: examples/scene_set.sample.json)",
    )
    parser.add_argument(
        "--config-dir",
        default=None,
        help="Configuration directory (default: <repo>/config)",
    )
    parser.add_argument("--job-id", default=None, help="Optional job identifier")
    parser.add_argument(
        "--provider",
        choices=["deterministic_whiteboard", "gemini_omni"],
        default=None,
        help="Override the configured provider",
    )
    parser.add_argument("--width", type=int, default=None, help="Output width")
    parser.add_argument("--height", type=int, default=None, help="Output height")
    parser.add_argument("--fps", type=int, default=None, help="Output frame rate")
    parser.add_argument("--no-intro", action="store_true", help="Disable intro card")
    parser.add_argument("--no-outro", action="store_true", help="Disable outro card")
    parser.add_argument(
        "--plain-logs", action="store_true", help="Use plain text logs instead of JSON"
    )
    parser.add_argument(
        "--log-level", default=None, help="Override the configured log level"
    )
    parser.add_argument(
        "--cancel-file",
        default=None,
        help="Cancellation flag file; creating it requests a cooperative stop",
    )
    return parser.parse_args(argv)


def _resolve_scene_set(path_value: str) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute() and not path.exists():
        path = ROOT / path
    return path.resolve()


def _apply_overrides(settings: Any, args: argparse.Namespace) -> Any:
    updates: dict[str, Any] = {}
    provider = settings.provider
    if args.provider is not None:
        provider = provider.model_copy(update={"id": args.provider})
    whiteboard = provider.whiteboard
    if args.width is not None:
        whiteboard = whiteboard.model_copy(update={"width": args.width})
    if args.height is not None:
        whiteboard = whiteboard.model_copy(update={"height": args.height})
    if args.fps is not None:
        whiteboard = whiteboard.model_copy(update={"fps": args.fps})
    provider = provider.model_copy(update={"whiteboard": whiteboard})
    updates["provider"] = provider

    assembly = settings.assembly
    assembly_updates: dict[str, Any] = {}
    if args.width is not None:
        assembly_updates["width"] = args.width
    if args.height is not None:
        assembly_updates["height"] = args.height
    if args.fps is not None:
        assembly_updates["fps"] = args.fps
    if args.no_intro:
        assembly_updates["include_intro"] = False
    if args.no_outro:
        assembly_updates["include_outro"] = False
    if assembly_updates:
        assembly = assembly.model_copy(update=assembly_updates)
    updates["assembly"] = assembly

    if args.log_level is not None:
        updates["log_level"] = args.log_level.upper()
    return settings.model_copy(update=updates)


def _summary(
    job: GenerationJob,
    scene_set: SceneSet,
    storage: LocalFileStorage,
) -> dict[str, Any]:
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


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        settings = load_settings(config_dir=args.config_dir)
        settings = _apply_overrides(settings, args)
        configure_logging(
            args.log_level or settings.log_level,
            json_logs=not args.plain_logs,
        )

        scene_path = _resolve_scene_set(args.scene_set)
        if not scene_path.is_file():
            raise FileNotFoundError(f"Scene-set file not found: {scene_path}")
        scene_set = SceneSet.model_validate_json(
            scene_path.read_text(encoding="utf-8")
        )

        media_tools = FFmpegTools(
            settings.media.ffmpeg_path,
            settings.media.ffprobe_path,
            timeout_seconds=settings.media.probe_timeout_seconds,
        )
        media_tools.ensure_available()

        storage = LocalFileStorage(settings.storage_root, temp_root=settings.temp_root)
        registry = ArtifactRegistry(settings.registry_path)
        artifact_storage = ArtifactStorageService(storage, registry)

        provider = build_provider(settings, media_tools)
        generation_service = SceneGenerationService(
            provider,
            artifact_storage,
            media_tools,
            max_attempts=settings.generation.max_attempts,
            timeout_seconds=settings.generation.timeout_seconds,
            backoff_base_seconds=settings.generation.backoff_base_seconds,
            backoff_max_seconds=settings.generation.backoff_max_seconds,
            backoff_jitter_seconds=settings.generation.backoff_jitter_seconds,
            sync_tolerance_seconds=settings.generation.sync_tolerance_seconds,
            sleep=time.sleep,
        )
        validator = SceneValidator(
            media_tools,
            storage,
            policy=settings.validation,
        )
        validation_agent = SceneValidationAgent(validator, artifact_storage)
        assembler = FFmpegAssembler(media_tools, settings.assembly)
        assembly_service = VideoAssemblyService(
            assembler,
            artifact_storage,
            media_tools,
            audio_silence_mean_db=settings.validation.audio_silence_mean_db,
            audio_silence_max_db=settings.validation.audio_silence_max_db,
        )

        cancel_path = (
            Path(args.cancel_file).expanduser().resolve()
            if args.cancel_file
            else None
        )

        def cancellation_check() -> bool:
            return bool(cancel_path and cancel_path.exists())

        def progress_callback(job: GenerationJob, message: str) -> None:
            logger.info(
                "Pipeline progress",
                extra={
                    "job_id": job.job_id,
                    "stage": job.stage.value,
                    "progress_percent": job.progress_percent,
                    "progress_message": message,
                },
            )

        orchestrator = WorkflowOrchestrator(
            generation_service,
            validation_agent,
            assembly_service,
            artifact_storage,
            max_validation_attempts=settings.generation.max_validation_attempts,
            progress_callback=progress_callback,
            cancellation_check=cancellation_check,
        )
        job = orchestrator.run(scene_set, job_id=args.job_id)
        summary = _summary(job, scene_set, storage)
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        if job.status.value == "completed":
            return 0
        if job.status.value == "cancelled":
            return 2
        return 1
    except AppError as exc:
        logger.error(
            "Pipeline aborted",
            extra={"error_code": exc.code, "error_details": exc.details},
        )
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": exc.as_dict(),
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        )
        return 1
    except (OSError, ValueError) as exc:
        logger.exception("Pipeline input/configuration error")
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
