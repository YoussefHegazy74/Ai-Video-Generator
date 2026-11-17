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

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = Path(__file__).resolve().parent
for extra_path in (SRC, SCRIPTS):
    if str(extra_path) not in sys.path:
        sys.path.insert(0, str(extra_path))

from ai_video_generator.core.exceptions import AppError  # noqa: E402
from ai_video_generator.core.logging import configure_logging, get_logger  # noqa: E402
from ai_video_generator.core.settings import load_settings  # noqa: E402
from ai_video_generator.models.generation_job import GenerationJob  # noqa: E402
from ai_video_generator.services.pipeline_factory import (  # noqa: E402
    build_runtime_pipeline,
)
from ai_video_generator.video.generation_contract import SceneSet  # noqa: E402

from _shared import apply_overrides, build_summary, resolve_input_path  # noqa: E402

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


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        settings = load_settings(config_dir=args.config_dir)
        settings = apply_overrides(settings, args)
        configure_logging(
            args.log_level or settings.log_level,
            json_logs=not args.plain_logs,
        )

        scene_path = resolve_input_path(args.scene_set)
        if not scene_path.is_file():
            raise FileNotFoundError(f"Scene-set file not found: {scene_path}")
        scene_set = SceneSet.model_validate_json(
            scene_path.read_text(encoding="utf-8")
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

        components = build_runtime_pipeline(
            settings,
            progress_callback=progress_callback,
            cancellation_check=cancellation_check,
            sleep=time.sleep,
        )
        components.media_tools.ensure_available()
        job = components.orchestrator.run(scene_set, job_id=args.job_id)
        summary = build_summary(job, scene_set, components.storage)
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
