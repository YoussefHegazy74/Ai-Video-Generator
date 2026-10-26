"""Video assembly service.

The service gates assembly on validated scenes and verified source artifacts,
renders in a job-scoped temporary directory, verifies the final media with
FFprobe/FFmpeg, and only then stores the final video and assembly manifest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict

from ai_video_generator.core.constants import ArtifactKind
from ai_video_generator.core.exceptions import (
    AppError,
    AssemblyInputError,
    AssemblyRenderError,
    AssemblyVerificationError,
    PipelineCancelledError,
)
from ai_video_generator.core.logging import get_logger
from ai_video_generator.services.storage_service import ArtifactStorageService
from ai_video_generator.utils.identifiers import safe_component
from ai_video_generator.video.assembly import (
    AssemblyManifest,
    AssemblyPlan,
    AssemblyVerification,
    FFmpegAssembler,
    SceneAssemblyArtifact,
)
from ai_video_generator.video.continuity import validate_scene_set_structure
from ai_video_generator.video.media_probe import FFmpegTools, MediaInfo, file_sha256
from ai_video_generator.video.scene_contract import SceneSet

logger = get_logger(__name__)

ProgressCallback = Callable[[str, float], None]
CancellationCheck = Callable[[], bool]


class AssemblyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final_video_key: str
    manifest_key: str
    manifest: AssemblyManifest
    verification: AssemblyVerification


class VideoAssemblyService:
    def __init__(
        self,
        assembler: FFmpegAssembler,
        storage: ArtifactStorageService,
        media_tools: FFmpegTools,
        *,
        audio_silence_mean_db: float = -50.0,
        audio_silence_max_db: float = -45.0,
    ) -> None:
        self.assembler = assembler
        self.storage = storage
        self.media_tools = media_tools
        self.audio_silence_mean_db = audio_silence_mean_db
        self.audio_silence_max_db = audio_silence_max_db

    def assemble(
        self,
        scene_set: SceneSet,
        *,
        job_id: str,
        progress: ProgressCallback | None = None,
        cancellation_check: CancellationCheck | None = None,
    ) -> AssemblyResult:
        safe_component(job_id, field="job_id")
        if cancellation_check is not None and cancellation_check():
            raise PipelineCancelledError(
                "Assembly cancelled before rendering",
                details={"job_id": job_id},
            )

        source_paths = self._gate_inputs(scene_set, job_id=job_id)
        try:
            plan = self.assembler.build_plan(scene_set, job_id, source_paths)
        except AssemblyInputError:
            raise
        except AppError as exc:
            raise AssemblyInputError(
                f"Assembly plan could not be built: {exc.message}",
                details=exc.details,
                cause=exc,
            ) from exc

        with self.storage.temporary_directory(f"asm-{job_id[:40]}") as work_dir:
            output_path = work_dir / f"final.{self.assembler.options.output_container}"
            try:
                self.assembler.render(
                    plan,
                    work_dir,
                    output_path,
                    progress=progress,
                )
            except AssemblyRenderError:
                raise
            except AppError as exc:
                raise AssemblyRenderError(
                    f"Assembly rendering failed: {exc.message}",
                    details=exc.details,
                    cause=exc,
                ) from exc
            if cancellation_check is not None and cancellation_check():
                raise PipelineCancelledError(
                    "Assembly cancelled after rendering and before storage",
                    details={"job_id": job_id},
                )

            if progress:
                progress("verifying_output", 0.98)
            verification = self._verify_output(output_path, plan)
            output_sha = file_sha256(output_path)
            size_bytes = output_path.stat().st_size
            final_key = f"{self.storage.job_prefix(job_id)}/final/final.{self.assembler.options.output_container}"
            final_record = self.storage.store_file(
                output_path,
                final_key,
                ArtifactKind.FINAL_VIDEO,
                job_id=job_id,
                metadata={
                    "duration_seconds": verification.duration_seconds,
                    "expected_duration_seconds": verification.expected_duration_seconds,
                    "scene_count": len(plan.scene_entries),
                },
            )
            manifest = AssemblyManifest(
                job_id=job_id,
                output_key=final_record.key,
                output_sha256=output_sha,
                size_bytes=size_bytes,
                duration_seconds=verification.duration_seconds,
                expected_duration_seconds=verification.expected_duration_seconds,
                width=verification.width,
                height=verification.height,
                fps=verification.fps,
                video_codec=verification.video_codec,
                audio_codec=verification.audio_codec,
                container=plan.options.output_container,
                scene_order=plan.scene_order,
                scenes=[
                    SceneAssemblyArtifact(
                        scene_id=entry.scene_id or "",
                        scene_index=entry.scene_index or 0,
                        artifact_key=next(
                            (
                                scene.media.video.artifact_key
                                for scene in scene_set.ordered_scenes
                                if scene.scene_id == entry.scene_id
                                and scene.media.video is not None
                            ),
                            "",
                        ),
                        sha256=entry.media_sha256 or "",
                        duration_seconds=entry.duration_seconds,
                    )
                    for entry in plan.scene_entries
                ],
                intro_included=any(entry.kind == "intro" for entry in plan.entries),
                outro_included=any(entry.kind == "outro" for entry in plan.entries),
                transition=plan.options.transition,
                transition_duration_seconds=plan.options.transition_duration_seconds,
                options=plan.options,
                verification=verification,
            )
            manifest_key = f"{self.storage.job_prefix(job_id)}/final/assembly_manifest.json"
            self.storage.store_json(
                manifest.model_dump(mode="json"),
                manifest_key,
                ArtifactKind.ASSEMBLY_MANIFEST,
                job_id=job_id,
                metadata={
                    "output_key": final_record.key,
                    "duration_seconds": verification.duration_seconds,
                },
            )
        logger.info(
            "Final video assembled and verified",
            extra={
                "job_id": job_id,
                "final_video_key": final_record.key,
                "duration_seconds": verification.duration_seconds,
                "scene_count": len(plan.scene_entries),
            },
        )
        return AssemblyResult(
            final_video_key=final_record.key,
            manifest_key=manifest_key,
            manifest=manifest,
            verification=verification,
        )

    # ----------------------------------------------------------------- internals

    def _gate_inputs(self, scene_set: SceneSet, *, job_id: str) -> dict[str, Path]:
        try:
            scene_set.require_assembly_ready()
        except AppError as exc:
            raise AssemblyInputError(
                f"Scene set is not assembly-ready: {exc.message}",
                details=exc.details,
                cause=exc,
            ) from exc

        continuity = validate_scene_set_structure(scene_set)
        if continuity.is_blocked:
            raise AssemblyInputError(
                "Scene set failed structural/style continuity checks",
                details={
                    "blocking_checks": [
                        check.model_dump(mode="json")
                        for check in continuity.blocking_failures
                    ]
                },
            )

        paths: dict[str, Path] = {}
        for scene in scene_set.ordered_scenes:
            video = scene.media.video
            if video is None:
                raise AssemblyInputError(
                    f"Scene {scene.scene_id!r} has no generated video",
                    details={"scene_id": scene.scene_id},
                )
            if not self.storage.exists(video.artifact_key):
                raise AssemblyInputError(
                    f"Scene video artifact is missing from storage: "
                    f"{video.artifact_key}",
                    details={"scene_id": scene.scene_id, "key": video.artifact_key},
                )
            path = self.storage.absolute_path(video.artifact_key)
            if not path.is_file() or path.stat().st_size <= 0:
                raise AssemblyInputError(
                    f"Scene video file is missing or empty: {path}",
                    details={"scene_id": scene.scene_id},
                )
            actual_sha = file_sha256(path)
            if actual_sha != video.sha256:
                raise AssemblyInputError(
                    f"Scene video hash changed after validation: {scene.scene_id}",
                    details={
                        "scene_id": scene.scene_id,
                        "expected_sha256": video.sha256,
                        "actual_sha256": actual_sha,
                    },
                )
            try:
                info = self.media_tools.probe(path)
            except AppError as exc:
                raise AssemblyInputError(
                    f"Scene video is unreadable: {scene.scene_id}",
                    details={"scene_id": scene.scene_id, "error": exc.message},
                    cause=exc,
                ) from exc
            if info.video is None:
                raise AssemblyInputError(
                    f"Scene media has no video stream: {scene.scene_id}",
                    details={"scene_id": scene.scene_id},
                )
            if info.audio is None:
                raise AssemblyInputError(
                    f"Scene media has no narration audio: {scene.scene_id}. "
                    "Silent scenes are not assembled.",
                    details={"scene_id": scene.scene_id},
                )
            paths[scene.scene_id] = path
        return paths

    def _verify_output(
        self, output_path: Path, plan: AssemblyPlan
    ) -> AssemblyVerification:
        notes: list[str] = []
        if not output_path.is_file():
            raise AssemblyVerificationError(
                "Assembly output file does not exist",
                details={"path": str(output_path)},
            )
        size_bytes = output_path.stat().st_size
        if size_bytes <= 0:
            raise AssemblyVerificationError(
                "Assembly output file is empty",
                details={"path": str(output_path)},
            )
        try:
            info: MediaInfo = self.media_tools.probe(output_path)
        except AppError as exc:
            raise AssemblyVerificationError(
                "Assembly output is unreadable",
                details={"path": str(output_path), "error": exc.message},
                cause=exc,
            ) from exc

        options = self.assembler.options
        if info.video is None:
            raise AssemblyVerificationError("Assembly output has no video stream")
        if info.audio is None:
            raise AssemblyVerificationError(
                "Assembly output has no audio stream; refusing silent final video"
            )
        if info.video.width != options.width or info.video.height != options.height:
            raise AssemblyVerificationError(
                "Assembly output dimensions do not match the configured output",
                details={
                    "expected": {"width": options.width, "height": options.height},
                    "actual": {
                        "width": info.video.width,
                        "height": info.video.height,
                    },
                },
            )
        actual_fps = info.video.fps
        if actual_fps is None or abs(actual_fps - options.fps) > 0.1:
            raise AssemblyVerificationError(
                "Assembly output frame rate does not match the configured output",
                details={"expected": options.fps, "actual": actual_fps},
            )
        if info.video.codec_name != "h264":
            raise AssemblyVerificationError(
                "Assembly output video codec is not H.264",
                details={"actual": info.video.codec_name},
            )
        if info.audio.codec_name != "aac":
            raise AssemblyVerificationError(
                "Assembly output audio codec is not AAC",
                details={"actual": info.audio.codec_name},
            )
        if options.output_container not in info.container_names:
            raise AssemblyVerificationError(
                "Assembly output container does not match the configured output",
                details={
                    "expected": options.output_container,
                    "container": info.format_name,
                },
            )
        duration_delta = abs(
            info.duration_seconds - plan.expected_duration_seconds
        )
        if duration_delta > options.duration_tolerance_seconds:
            raise AssemblyVerificationError(
                "Assembly output duration differs from the planned duration",
                details={
                    "expected_seconds": plan.expected_duration_seconds,
                    "actual_seconds": info.duration_seconds,
                    "delta_seconds": duration_delta,
                    "tolerance_seconds": options.duration_tolerance_seconds,
                },
            )
        if info.audio_video_duration_delta_seconds is not None and (
            info.audio_video_duration_delta_seconds > max(0.5, options.duration_tolerance_seconds)
        ):
            raise AssemblyVerificationError(
                "Final audio and video stream durations are misaligned",
                details={
                    "delta_seconds": info.audio_video_duration_delta_seconds,
                },
            )

        try:
            levels = self.media_tools.measure_audio_level(output_path)
        except AppError as exc:
            raise AssemblyVerificationError(
                "Final audio level could not be measured",
                details={"error": exc.message},
                cause=exc,
            ) from exc
        audio_not_silent = not (
            levels.max_db < self.audio_silence_max_db
            or levels.mean_db < self.audio_silence_mean_db
        )
        if not audio_not_silent:
            raise AssemblyVerificationError(
                "Final video narration is silent or effectively inaudible",
                details={"mean_db": levels.mean_db, "max_db": levels.max_db},
            )

        notes.append(
            "Scene order is enforced by the explicit concatenation plan; it is "
            "not independently detected from pixels."
        )
        notes.append(
            "Semantic narration-to-visual alignment is not evaluated."
        )
        return AssemblyVerification(
            output_exists=True,
            output_non_empty=True,
            duration_seconds=info.duration_seconds,
            expected_duration_seconds=plan.expected_duration_seconds,
            duration_within_tolerance=True,
            video_stream_present=True,
            audio_stream_present=True,
            width=info.video.width,
            height=info.video.height,
            fps=info.video.fps,
            video_codec=info.video.codec_name,
            audio_codec=info.audio.codec_name,
            scene_order=plan.scene_order,
            audio_not_silent=audio_not_silent,
            notes=notes,
        )


__all__ = ["AssemblyResult", "VideoAssemblyService"]
