#!/usr/bin/env python
"""Run the integrated lesson -> video pipeline from pre-planned artifacts.

This entry point covers everything implemented end to end:

    LessonPlan JSON
      + either per-scene planning artifacts, or a live Gemini planning run
      -> ScenePackager
      -> SceneRuntimeAdapter
      -> generate -> validate -> assemble
      -> verified final video

Without ``--planning-dir`` the Script, Whiteboard Director, and Example & Visual
agents run through the configured Gemini adapter, which requires
``GEMINI_API_KEY``. With ``--planning-dir`` the deterministic packaging path runs
from pre-generated artifacts and needs no credentials. Use ``--plan-only`` to stop
after packaging and adaptation. To run only the media stage from a ready
SceneSet, use ``scripts/run_pipeline.py``.

Document ingestion and lesson planning are still not implemented; the input is an
authored ``LessonPlan``.

The default renderer is the local deterministic whiteboard renderer, not an AI
video model. FFmpeg plus a local TTS path (Windows SAPI or FFmpeg flite) are
required for media generation.
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
SCRIPTS = Path(__file__).resolve().parent
for extra_path in (SRC, SCRIPTS):
    if str(extra_path) not in sys.path:
        sys.path.insert(0, str(extra_path))

from ai_video_generator.agents.orchestration.runtime_adapter import (  # noqa: E402
    RuntimeAdapterError,
    RuntimeSceneOptions,
    SceneRuntimeAdapter,
)
from ai_video_generator.agents.scene_planning.errors import (  # noqa: E402
    ScenePlanningError,
)
from ai_video_generator.agents.scene_packaging.scene_packager_agent import (  # noqa: E402
    ScenePackager,
    ScenePackagingError,
)
from ai_video_generator.core.exceptions import AppError  # noqa: E402
from ai_video_generator.core.logging import configure_logging, get_logger  # noqa: E402
from ai_video_generator.core.settings import load_settings  # noqa: E402
from ai_video_generator.models.generation_job import GenerationJob  # noqa: E402
from ai_video_generator.models.lesson_plan import LessonPlan  # noqa: E402
from ai_video_generator.models.scene_planning import (  # noqa: E402
    ScenePlanningResult,
)
from ai_video_generator.models.visual_planning import (  # noqa: E402
    VisualPlanningResult,
)
from ai_video_generator.services.pipeline_factory import (  # noqa: E402
    build_planning_pipeline,
    build_runtime_pipeline,
)
from ai_video_generator.video.scene_timing import DurationLimits  # noqa: E402

from _shared import (  # noqa: E402
    apply_overrides,
    build_summary,
    resolve_input_path,
)

logger = get_logger(__name__)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lesson-plan",
        required=True,
        help="LessonPlan JSON file",
    )
    parser.add_argument(
        "--planning-dir",
        default=None,
        help=(
            "Directory with <scene_id>.scene_planning.json and "
            "<scene_id>.visual_planning.json for every lesson scene. Omit to run "
            "the live Gemini planning agents (requires GEMINI_API_KEY)."
        ),
    )
    parser.add_argument(
        "--save-planning-dir",
        default=None,
        help=(
            "When running live planning, write the validated planning artifacts "
            "to this directory so later runs can use --planning-dir"
        ),
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Package and adapt scenes without generating media",
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


def _write_planning_artifacts(
    packages: list[Any],
    lesson_plan: LessonPlan,
    directory: Path,
) -> None:
    """Persist validated live planning output in the artifact CLI format."""
    directory.mkdir(parents=True, exist_ok=True)
    for package, brief in zip(packages, lesson_plan.scenes):
        planning_result = ScenePlanningResult(
            lesson_id=lesson_plan.lesson_id,
            scene=brief,
            script=package.script,
            whiteboard=package.whiteboard,
        )
        visual_result = VisualPlanningResult(
            scene_id=package.scene_id,
            plan=package.visual,
        )
        (directory / f"{package.scene_id}.scene_planning.json").write_text(
            json.dumps(planning_result.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )
        (directory / f"{package.scene_id}.visual_planning.json").write_text(
            json.dumps(visual_result.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )


def _read_json(path: Path) -> Any:
    if not path.is_file():
        raise FileNotFoundError(f"Planning artifact not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_lesson_plan(path: Path) -> LessonPlan:
    return LessonPlan.from_dict(_read_json(path))


def _load_planning_artifacts(
    planning_dir: Path, lesson_plan: LessonPlan
) -> list[tuple[ScenePlanningResult, VisualPlanningResult]]:
    artifacts: list[tuple[ScenePlanningResult, VisualPlanningResult]] = []
    for brief in lesson_plan.scenes:
        scene_path = planning_dir / f"{brief.scene_id}.scene_planning.json"
        visual_path = planning_dir / f"{brief.scene_id}.visual_planning.json"
        planning_result = ScenePlanningResult.from_dict(_read_json(scene_path))
        visual_result = VisualPlanningResult.from_dict(_read_json(visual_path))
        if planning_result.scene.scene_id != brief.scene_id:
            raise ValueError(
                f"{scene_path} describes scene "
                f"{planning_result.scene.scene_id!r}, expected {brief.scene_id!r}"
            )
        if visual_result.scene_id != brief.scene_id:
            raise ValueError(
                f"{visual_path} describes scene {visual_result.scene_id!r}, "
                f"expected {brief.scene_id!r}"
            )
        artifacts.append((planning_result, visual_result))
    return artifacts


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        settings = load_settings(config_dir=args.config_dir)
        settings = apply_overrides(settings, args)
        configure_logging(
            args.log_level or settings.log_level,
            json_logs=not args.plain_logs,
        )

        lesson_plan = _load_lesson_plan(resolve_input_path(args.lesson_plan))

        if args.planning_dir:
            artifacts = _load_planning_artifacts(
                resolve_input_path(args.planning_dir), lesson_plan
            )
            duration_limits = DurationLimits(
                minimum_seconds=settings.planning.min_scene_duration_seconds,
                maximum_seconds=settings.planning.max_scene_duration_seconds,
                tolerance_seconds=settings.planning.duration_tolerance_seconds,
            )
            packager = ScenePackager()
            packages = [
                packager.package(planning_result, visual_result, duration_limits)
                for planning_result, visual_result in artifacts
            ]
        else:
            logger.info(
                "Running live planning",
                extra={"model": settings.llm.model, "provider": settings.llm.provider},
            )
            planning_components = build_planning_pipeline(settings)
            packages = list(
                planning_components.planning.plan_lesson(lesson_plan)
            )
            if args.save_planning_dir:
                _write_planning_artifacts(
                    packages,
                    lesson_plan,
                    resolve_input_path(args.save_planning_dir),
                )

        scene_set = SceneRuntimeAdapter().adapt_lesson(
            packages,
            lesson_plan,
            options=RuntimeSceneOptions(
                whiteboard_style=settings.planning.whiteboard_style,
                background_color=settings.planning.background_color,
                accent_color=settings.planning.accent_color,
                show_scene_title=settings.planning.show_scene_title,
            ),
        )

        if args.plan_only:
            print(
                json.dumps(
                    {
                        "status": "planned",
                        "lesson_id": lesson_plan.lesson_id,
                        "scene_set": scene_set.model_dump(mode="json"),
                    },
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )
            )
            return 0

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
        summary["lesson_id"] = lesson_plan.lesson_id
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        if job.status.value == "completed":
            return 0
        if job.status.value == "cancelled":
            return 2
        return 1
    except (
        AppError,
        RuntimeAdapterError,
        ScenePackagingError,
        ScenePlanningError,
        ValueError,
        OSError,
    ) as exc:
        logger.error(
            "Lesson pipeline aborted",
            extra={"error": f"{type(exc).__name__}: {exc}"},
        )
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
