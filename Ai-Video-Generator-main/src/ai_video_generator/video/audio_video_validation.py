"""Measurable scene validation and structured reports.

The validator distinguishes what it can actually measure from what it cannot.
OCR, semantic narration-to-visual alignment, and source-accuracy scoring are not
implemented; those checks are explicitly reported as unavailable.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any, Protocol

from PIL import Image, ImageStat
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_video_generator.core.constants import (
    VALIDATION_POLICY_VERSION,
    CheckStatus,
    SceneStatus,
    Severity,
    TextRenderMode,
)
from ai_video_generator.core.exceptions import MediaProbeError, StorageError
from ai_video_generator.video.media_probe import (
    FFmpegTools,
    MediaInfo,
    file_sha256,
)
from ai_video_generator.video.scene_contract import (
    SceneSpec,
    ValidationCheck,
    ValidationReport,
)
from ai_video_generator.video.scene_timing import within_duration_range

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ValidationStorage(Protocol):
    def absolute_path(self, key: str) -> Path: ...

    def exists(self, key: str) -> bool: ...

    def read_json(self, key: str) -> Any: ...


class ValidationPolicy(BaseModel):
    """Configurable validation thresholds and blocking behavior."""

    model_config = ConfigDict(extra="forbid")

    require_audio: bool = True
    duration_warning_tolerance_seconds: float = Field(default=1.5, ge=0)
    duration_failure_tolerance_seconds: float = Field(default=3.0, ge=0)
    enforce_project_duration_range: bool = True
    min_scene_duration_seconds: float = Field(default=8.0, gt=0)
    max_scene_duration_seconds: float = Field(default=15.0, gt=0)
    sync_tolerance_seconds: float = Field(default=0.35, ge=0)
    sync_failure_tolerance_seconds: float = Field(default=0.9, ge=0)
    min_width: int = Field(default=640, ge=16)
    min_height: int = Field(default=360, ge=16)
    min_fps: float = Field(default=8.0, gt=0)
    min_media_bytes: int = Field(default=1024, ge=1)
    audio_silence_mean_db: float = -50.0
    audio_silence_max_db: float = -45.0
    black_frame_warning_ratio: float = Field(default=0.5, ge=0, le=1)
    black_frame_failure_ratio: float = Field(default=0.9, ge=0, le=1)
    blank_frame_stddev_threshold: float = Field(default=2.0, ge=0)
    supported_video_codecs: list[str] = Field(
        default_factory=lambda: ["h264", "hevc", "vp9", "av1", "mpeg4"]
    )
    supported_audio_codecs: list[str] = Field(
        default_factory=lambda: [
            "aac",
            "mp3",
            "opus",
            "vorbis",
            "flac",
            "pcm_s16le",
            "pcm_s24le",
        ]
    )
    supported_containers: list[str] = Field(
        default_factory=lambda: ["mp4", "mov", "webm", "matroska"]
    )
    block_on_warnings: bool = False

    @model_validator(mode="after")
    def _consistent_thresholds(self) -> "ValidationPolicy":
        if self.min_scene_duration_seconds > self.max_scene_duration_seconds:
            raise ValueError("min_scene_duration_seconds exceeds maximum")
        if (
            self.duration_warning_tolerance_seconds
            > self.duration_failure_tolerance_seconds
        ):
            raise ValueError("duration warning tolerance exceeds failure tolerance")
        if self.sync_tolerance_seconds > self.sync_failure_tolerance_seconds:
            raise ValueError("sync tolerance exceeds failure tolerance")
        if self.black_frame_warning_ratio > self.black_frame_failure_ratio:
            raise ValueError("black warning ratio exceeds failure ratio")
        return self


class _Checks:
    def __init__(self) -> None:
        self.items: list[ValidationCheck] = []

    def add(
        self,
        check_id: str,
        category: str,
        status: CheckStatus,
        severity: Severity,
        message: str,
        *,
        expected: Any | None = None,
        observed: Any | None = None,
        details: dict[str, Any] | None = None,
        blocking: bool = True,
        retryable: bool = False,
    ) -> None:
        self.items.append(
            ValidationCheck(
                check_id=check_id,
                category=category,
                status=status,
                severity=severity,
                message=message,
                expected=expected,
                observed=observed,
                details=details or {},
                blocking=blocking,
                retryable=retryable,
            )
        )

    def pass_(
        self,
        check_id: str,
        category: str,
        message: str,
        **kwargs: Any,
    ) -> None:
        self.add(
            check_id,
            category,
            CheckStatus.PASS,
            Severity.INFO,
            message,
            **kwargs,
        )

    def fail(
        self,
        check_id: str,
        category: str,
        message: str,
        *,
        severity: Severity = Severity.ERROR,
        **kwargs: Any,
    ) -> None:
        self.add(
            check_id,
            category,
            CheckStatus.FAIL,
            severity,
            message,
            **kwargs,
        )

    def warn(
        self,
        check_id: str,
        category: str,
        message: str,
        **kwargs: Any,
    ) -> None:
        self.add(
            check_id,
            category,
            CheckStatus.WARNING,
            Severity.WARNING,
            message,
            **kwargs,
        )

    def unavailable(
        self,
        check_id: str,
        category: str,
        message: str,
        **kwargs: Any,
    ) -> None:
        self.add(
            check_id,
            category,
            CheckStatus.UNAVAILABLE,
            Severity.INFO,
            message,
            blocking=False,
            **kwargs,
        )


def _normalize_texts(values: list[str]) -> list[str]:
    return [" ".join(value.split()) for value in values]


class SceneValidator:
    def __init__(
        self,
        media_tools: FFmpegTools,
        storage: ValidationStorage,
        policy: ValidationPolicy | None = None,
    ) -> None:
        self.media_tools = media_tools
        self.storage = storage
        self.policy = policy or ValidationPolicy()

    def validate(
        self,
        scene: SceneSpec,
        *,
        expected_style: str | None = None,
        scene_ids: list[str] | None = None,
        total_scenes: int | None = None,
    ) -> ValidationReport:
        checks = _Checks()
        self._structural_checks(checks, scene, scene_ids, total_scenes)

        video_info: MediaInfo | None = None
        audio_info: MediaInfo | None = None
        video_path: Path | None = None
        audio_path: Path | None = None

        if scene.media.video is None:
            checks.fail(
                "media.video_required",
                "media",
                "Scene has no generated video artifact; generation has not completed.",
                expected="a stored video artifact",
                observed=None,
                retryable=True,
            )
        else:
            video_path = self.storage.absolute_path(scene.media.video.artifact_key)
            if not self.storage.exists(scene.media.video.artifact_key):
                checks.fail(
                    "media.video_exists",
                    "media",
                    "Referenced video artifact does not exist in storage.",
                    expected=scene.media.video.artifact_key,
                    observed="missing",
                    retryable=True,
                )
            else:
                size = video_path.stat().st_size
                if size < self.policy.min_media_bytes:
                    checks.fail(
                        "media.video_size",
                        "media",
                        "Video artifact is suspiciously small and may be empty or truncated.",
                        expected=f">= {self.policy.min_media_bytes} bytes",
                        observed=size,
                        retryable=True,
                    )
                else:
                    checks.pass_(
                        "media.video_size",
                        "media",
                        "Video artifact is non-empty.",
                        observed=size,
                    )
                try:
                    video_info = self.media_tools.probe(video_path)
                    checks.pass_(
                        "media.video_readable",
                        "media",
                        "Video metadata was read successfully.",
                        observed={
                            "format": video_info.format_name,
                            "duration": video_info.duration_seconds,
                        },
                    )
                except MediaProbeError as exc:
                    checks.fail(
                        "media.video_readable",
                        "media",
                        "Video is corrupt, truncated, or unreadable.",
                        observed=exc.message,
                        details=exc.details,
                        retryable=True,
                    )

        if video_info is not None:
            self._video_checks(checks, scene, video_info)
            if self.policy.require_audio:
                audio_info, audio_path = self._audio_checks(
                    checks, scene, video_info, video_path
                )
            if video_info.audio is not None or audio_info is not None:
                self._sync_checks(checks, scene, video_info, audio_info, audio_path)
            self._duration_checks(checks, scene, video_info)
            self._visual_checks(checks, scene, video_info, video_path)
            self._text_checks(checks, scene)
            self._style_checks(checks, scene, expected_style)
            self._safety_checks(checks, scene, video_info, video_path)

        report = ValidationReport(
            scene_id=scene.scene_id,
            policy_version=VALIDATION_POLICY_VERSION,
            checks=checks.items,
            block_on_warnings=self.policy.block_on_warnings,
        )
        return report

    # ------------------------------------------------------------------ checks

    def _structural_checks(
        self,
        checks: _Checks,
        scene: SceneSpec,
        scene_ids: list[str] | None,
        total_scenes: int | None,
    ) -> None:
        if _IDENTIFIER_RE.fullmatch(scene.scene_id):
            checks.pass_("contract.scene_id", "structural", "Scene identifier is valid.")
        else:
            checks.fail(
                "contract.scene_id",
                "structural",
                "Scene identifier is invalid.",
                observed=scene.scene_id,
            )

        if scene.scene_index < 0:
            checks.fail(
                "contract.scene_index",
                "structural",
                "Scene index must be non-negative.",
                observed=scene.scene_index,
            )
        elif total_scenes is not None and scene.scene_index >= total_scenes:
            checks.fail(
                "contract.scene_index",
                "structural",
                "Scene index is outside the job's scene count.",
                expected=f"0..{total_scenes - 1}",
                observed=scene.scene_index,
            )
        else:
            checks.pass_(
                "contract.scene_index",
                "structural",
                "Scene index is valid.",
                observed=scene.scene_index,
            )

        if scene_ids is not None:
            count = scene_ids.count(scene.scene_id)
            if count != 1:
                checks.fail(
                    "contract.scene_id_unique",
                    "structural",
                    "Scene identifier is missing or duplicated in the scene set.",
                    expected="exactly one occurrence",
                    observed=count,
                )
            else:
                checks.pass_(
                    "contract.scene_id_unique",
                    "structural",
                    "Scene identifier is unique in the scene set.",
                )

        missing = []
        if not scene.title.strip():
            missing.append("title")
        if not scene.learning_objective.strip():
            missing.append("learning_objective")
        if not scene.narration.strip():
            missing.append("narration")
        if not scene.visual.instructions:
            missing.append("visual.instructions")
        if missing:
            checks.fail(
                "contract.required_content",
                "structural",
                "Scene is missing required educational content.",
                expected="title, objective, narration, and visual instructions",
                observed={"missing": missing},
            )
        else:
            checks.pass_(
                "contract.required_content",
                "structural",
                "Required educational content is present.",
            )

        if scene.status not in {
            SceneStatus.GENERATED,
            SceneStatus.VALIDATING,
            SceneStatus.VALIDATED,
        }:
            checks.fail(
                "contract.generation_completed",
                "structural",
                "Scene generation has not completed successfully.",
                expected="generated, validating, or validated",
                observed=scene.status.value,
            )
        else:
            checks.pass_(
                "contract.generation_completed",
                "structural",
                "Scene generation has completed.",
                observed=scene.status.value,
            )

        if scene.generation is None:
            checks.fail(
                "contract.generation_metadata",
                "structural",
                "Scene has no provider/generation metadata.",
            )
        else:
            checks.pass_(
                "contract.generation_metadata",
                "structural",
                "Generation metadata is present.",
                observed={
                    "provider": scene.generation.provider,
                    "requested_duration": scene.generation.requested_duration_seconds,
                },
            )

    def _video_checks(
        self, checks: _Checks, scene: SceneSpec, info: MediaInfo
    ) -> None:
        if info.video is None:
            checks.fail(
                "media.video_stream",
                "media",
                "Media file contains no video stream.",
                retryable=True,
            )
            return
        checks.pass_(
            "media.video_stream",
            "media",
            "Video stream is present.",
            observed={
                "codec": info.video.codec_name,
                "width": info.video.width,
                "height": info.video.height,
                "fps": info.video.fps,
            },
        )

        if info.video_streams > 1:
            checks.warn(
                "media.multiple_video_streams",
                "media",
                "More than one video stream is present; the first stream is used.",
                observed=info.video_streams,
            )

        containers = info.container_names
        supported = containers.intersection(self.policy.supported_containers)
        if supported:
            checks.pass_(
                "media.container",
                "media",
                "Container format is supported.",
                observed=sorted(containers),
            )
        else:
            checks.fail(
                "media.container",
                "media",
                "Container format is not in the supported list.",
                expected=self.policy.supported_containers,
                observed=sorted(containers),
            )

        if info.video.codec_name in self.policy.supported_video_codecs:
            checks.pass_(
                "media.video_codec",
                "media",
                "Video codec is supported.",
                observed=info.video.codec_name,
            )
        else:
            checks.fail(
                "media.video_codec",
                "media",
                "Video codec is not supported.",
                expected=self.policy.supported_video_codecs,
                observed=info.video.codec_name,
            )

        width = info.video.width or 0
        height = info.video.height or 0
        if width >= self.policy.min_width and height >= self.policy.min_height:
            checks.pass_(
                "media.dimensions",
                "media",
                "Video dimensions meet the minimum clarity requirement.",
                expected={
                    "min_width": self.policy.min_width,
                    "min_height": self.policy.min_height,
                },
                observed={"width": width, "height": height},
            )
        else:
            checks.fail(
                "media.dimensions",
                "media",
                "Video dimensions are below the configured minimum.",
                expected={
                    "min_width": self.policy.min_width,
                    "min_height": self.policy.min_height,
                },
                observed={"width": width, "height": height},
            )

        fps = info.video.fps or 0
        if fps >= self.policy.min_fps:
            checks.pass_(
                "media.frame_rate",
                "media",
                "Frame rate meets the minimum requirement.",
                expected=f">= {self.policy.min_fps}",
                observed=fps,
            )
        else:
            checks.fail(
                "media.frame_rate",
                "media",
                "Frame rate is below the configured minimum.",
                expected=f">= {self.policy.min_fps}",
                observed=fps,
            )

        if info.video.duration_seconds is None:
            checks.warn(
                "media.stream_duration",
                "media",
                "Video stream duration is not present in container metadata.",
                observed=None,
            )

    def _audio_checks(
        self,
        checks: _Checks,
        scene: SceneSpec,
        video_info: MediaInfo,
        video_path: Path,
    ) -> tuple[MediaInfo | None, Path | None]:
        if scene.media.audio_embedded and video_info.audio is not None:
            checks.pass_(
                "media.audio_required",
                "media",
                "Embedded audio stream is present.",
                observed={
                    "codec": video_info.audio.codec_name,
                    "sample_rate": video_info.audio.sample_rate,
                    "channels": video_info.audio.channels,
                },
            )
            self._audio_codec_check(checks, video_info.audio.codec_name)
            return video_info, video_path

        if scene.media.audio is not None:
            audio_path = self.storage.absolute_path(scene.media.audio.artifact_key)
            if not self.storage.exists(scene.media.audio.artifact_key):
                checks.fail(
                    "media.audio_exists",
                    "media",
                    "Referenced audio artifact does not exist.",
                    expected=scene.media.audio.artifact_key,
                    observed="missing",
                    retryable=True,
                )
                return None, None
            try:
                audio_info = self.media_tools.probe(audio_path)
            except MediaProbeError as exc:
                checks.fail(
                    "media.audio_readable",
                    "media",
                    "Audio artifact is corrupt or unreadable.",
                    observed=exc.message,
                    details=exc.details,
                    retryable=True,
                )
                return None, None
            if audio_info.audio is None:
                checks.fail(
                    "media.audio_stream",
                    "media",
                    "Audio file contains no audio stream.",
                    retryable=True,
                )
                return audio_info, audio_path
            checks.pass_(
                "media.audio_required",
                "media",
                "Separate audio stream is present.",
                observed={
                    "codec": audio_info.audio.codec_name,
                    "sample_rate": audio_info.audio.sample_rate,
                    "channels": audio_info.audio.channels,
                },
            )
            self._audio_codec_check(checks, audio_info.audio.codec_name)
            return audio_info, audio_path

        checks.fail(
            "media.audio_required",
            "media",
            "Required narration audio is missing. The scene must not be assembled "
            "with silent narration.",
            expected="embedded or separate audio stream",
            observed=None,
            retryable=True,
        )
        return None, None

    def _audio_codec_check(self, checks: _Checks, codec: str) -> None:
        if codec in self.policy.supported_audio_codecs:
            checks.pass_(
                "media.audio_codec",
                "media",
                "Audio codec is supported.",
                observed=codec,
            )
        else:
            checks.fail(
                "media.audio_codec",
                "media",
                "Audio codec is not supported.",
                expected=self.policy.supported_audio_codecs,
                observed=codec,
            )

    def _duration_checks(
        self, checks: _Checks, scene: SceneSpec, info: MediaInfo
    ) -> None:
        actual = info.duration_seconds
        target = scene.target_duration_seconds
        delta = abs(actual - target)
        expected = {
            "target_seconds": target,
            "warning_tolerance": self.policy.duration_warning_tolerance_seconds,
            "failure_tolerance": self.policy.duration_failure_tolerance_seconds,
        }
        if delta > self.policy.duration_failure_tolerance_seconds:
            checks.fail(
                "duration.target_delta",
                "duration",
                "Measured duration differs from the target beyond the failure tolerance.",
                expected=expected,
                observed={"actual_seconds": actual, "delta_seconds": delta},
            )
        elif delta > self.policy.duration_warning_tolerance_seconds:
            checks.warn(
                "duration.target_delta",
                "duration",
                "Measured duration differs from the target beyond the warning tolerance.",
                expected=expected,
                observed={"actual_seconds": actual, "delta_seconds": delta},
            )
        else:
            checks.pass_(
                "duration.target_delta",
                "duration",
                "Measured duration is within tolerance of the target.",
                expected=expected,
                observed={"actual_seconds": actual, "delta_seconds": delta},
            )

        if self.policy.enforce_project_duration_range:
            in_range = within_duration_range(
                actual,
                self.policy.min_scene_duration_seconds,
                self.policy.max_scene_duration_seconds,
            )
            if in_range:
                checks.pass_(
                    "duration.project_range",
                    "duration",
                    "Measured duration is within the configured scene duration range.",
                    expected={
                        "min": self.policy.min_scene_duration_seconds,
                        "max": self.policy.max_scene_duration_seconds,
                    },
                    observed=actual,
                )
            else:
                deviation = 0.0
                if actual < self.policy.min_scene_duration_seconds:
                    deviation = self.policy.min_scene_duration_seconds - actual
                elif actual > self.policy.max_scene_duration_seconds:
                    deviation = actual - self.policy.max_scene_duration_seconds
                if deviation > self.policy.duration_warning_tolerance_seconds:
                    checks.fail(
                        "duration.project_range",
                        "duration",
                        "Measured duration is outside the configured scene duration range.",
                        expected={
                            "min": self.policy.min_scene_duration_seconds,
                            "max": self.policy.max_scene_duration_seconds,
                        },
                        observed=actual,
                    )
                else:
                    checks.warn(
                        "duration.project_range",
                        "duration",
                        "Measured duration is slightly outside the configured scene range.",
                        expected={
                            "min": self.policy.min_scene_duration_seconds,
                            "max": self.policy.max_scene_duration_seconds,
                        },
                        observed=actual,
                    )

    def _sync_checks(
        self,
        checks: _Checks,
        scene: SceneSpec,
        video_info: MediaInfo,
        audio_info: MediaInfo | None,
        audio_path: Path | None,
    ) -> None:
        if audio_info is None:
            video_duration = video_info.video_duration_seconds
            audio_duration = video_info.audio_duration_seconds
            stream_durations = video_info.stream_durations_available
        else:
            video_duration = video_info.video_duration_seconds
            audio_duration = audio_info.audio_duration_seconds
            stream_durations = (
                video_info.video_duration_seconds is not None
                and audio_info.audio_duration_seconds is not None
            )

        if video_duration is None or audio_duration is None or not stream_durations:
            checks.unavailable(
                "sync.track_alignment",
                "synchronization",
                "Per-stream durations are unavailable; audio/video alignment could "
                "not be measured. Format-level duration is not sufficient evidence.",
                expected="video and audio stream durations",
                observed={
                    "video_duration": video_duration,
                    "audio_duration": audio_duration,
                },
            )
        else:
            delta = abs(video_duration - audio_duration)
            if delta <= self.policy.sync_tolerance_seconds:
                checks.pass_(
                    "sync.track_alignment",
                    "synchronization",
                    "Audio and video stream durations are aligned within tolerance. "
                    "This is a timing check, not semantic narration-to-visual review.",
                    expected=f"<= {self.policy.sync_tolerance_seconds}s",
                    observed={"delta_seconds": delta},
                )
            elif delta <= self.policy.sync_failure_tolerance_seconds:
                checks.warn(
                    "sync.track_alignment",
                    "synchronization",
                    "Audio and video stream durations differ beyond the warning tolerance.",
                    expected=f"<= {self.policy.sync_tolerance_seconds}s",
                    observed={"delta_seconds": delta},
                )
            else:
                checks.fail(
                    "sync.track_alignment",
                    "synchronization",
                    "Audio and video stream durations are misaligned beyond the failure tolerance.",
                    expected=f"<= {self.policy.sync_failure_tolerance_seconds}s",
                    observed={"delta_seconds": delta},
                )

        if audio_info is None:
            start_delta = video_info.audio_video_start_delta_seconds
        elif video_info.video is not None and audio_info.audio is not None:
            video_start = video_info.video.start_time_seconds
            audio_start = audio_info.audio.start_time_seconds
            start_delta = (
                abs(video_start - audio_start)
                if video_start is not None and audio_start is not None
                else None
            )
        else:
            start_delta = None
        if start_delta is not None:
            if start_delta <= self.policy.sync_tolerance_seconds:
                checks.pass_(
                    "sync.start_offset",
                    "synchronization",
                    "Audio/video stream start offsets are aligned.",
                    expected=f"<= {self.policy.sync_tolerance_seconds}s",
                    observed=start_delta,
                )
            elif start_delta <= self.policy.sync_failure_tolerance_seconds:
                checks.warn(
                    "sync.start_offset",
                    "synchronization",
                    "Audio/video stream start offsets differ.",
                    expected=f"<= {self.policy.sync_tolerance_seconds}s",
                    observed=start_delta,
                )
            else:
                checks.fail(
                    "sync.start_offset",
                    "synchronization",
                    "Audio/video stream start offsets are misaligned.",
                    expected=f"<= {self.policy.sync_failure_tolerance_seconds}s",
                    observed=start_delta,
                )

        level_path = audio_path if audio_path is not None else video_info.path
        try:
            levels = self.media_tools.measure_audio_level(level_path)
            if (
                levels.max_db < self.policy.audio_silence_max_db
                or levels.mean_db < self.policy.audio_silence_mean_db
            ):
                checks.fail(
                    "sync.audio_not_silent",
                    "synchronization",
                    "Narration audio appears silent or effectively inaudible.",
                    expected={
                        "mean_db_gt": self.policy.audio_silence_mean_db,
                        "max_db_gt": self.policy.audio_silence_max_db,
                    },
                    observed={"mean_db": levels.mean_db, "max_db": levels.max_db},
                    retryable=True,
                )
            else:
                checks.pass_(
                    "sync.audio_not_silent",
                    "synchronization",
                    "Narration audio is not silent.",
                    observed={"mean_db": levels.mean_db, "max_db": levels.max_db},
                )
        except MediaProbeError as exc:
            checks.unavailable(
                "sync.audio_not_silent",
                "synchronization",
                "Audio level could not be measured.",
                observed=exc.message,
                details=exc.details,
            )

        checks.unavailable(
            "sync.semantic_alignment",
            "synchronization",
            "Semantic synchronization between narration and visual events is not "
            "evaluated automatically; track timing only was measured.",
            expected="human or model-based semantic review",
            observed="not implemented",
        )
        checks.pass_(
            "sync.scene_metadata",
            "synchronization",
            "Stored sync metadata is present."
            if scene.sync is not None
            else "No sync metadata was stored; timing was measured directly from media.",
            observed=scene.sync.model_dump(mode="json") if scene.sync else None,
        )

    def _visual_checks(
        self,
        checks: _Checks,
        scene: SceneSpec,
        info: MediaInfo,
        video_path: Path,
    ) -> None:
        duration = info.duration_seconds
        timestamps = [duration * fraction for fraction in (0.1, 0.5, 0.9)]
        try:
            with tempfile.TemporaryDirectory(prefix="aivg-validation-") as temp:
                temp_path = Path(temp)
                deviations: list[float] = []
                for index, timestamp in enumerate(timestamps):
                    frame_path = temp_path / f"frame-{index}.png"
                    self.media_tools.extract_frame(
                        video_path,
                        min(timestamp, max(0.0, duration - 0.05)),
                        frame_path,
                    )
                    with Image.open(frame_path) as image:
                        deviations.append(
                            float(ImageStat.Stat(image.convert("L")).stddev[0])
                        )
            if all(
                deviation < self.policy.blank_frame_stddev_threshold
                for deviation in deviations
            ):
                checks.fail(
                    "visual.blank_frames",
                    "visual",
                    "Sampled frames appear blank/uniform; the output is not readable "
                    "whiteboard content.",
                    expected=f"stddev >= {self.policy.blank_frame_stddev_threshold}",
                    observed={"stddev": deviations},
                    retryable=True,
                )
            elif any(
                deviation < self.policy.blank_frame_stddev_threshold
                for deviation in deviations
            ):
                checks.warn(
                    "visual.blank_frames",
                    "visual",
                    "At least one sampled frame appears blank/uniform.",
                    expected=f"stddev >= {self.policy.blank_frame_stddev_threshold}",
                    observed={"stddev": deviations},
                )
            else:
                checks.pass_(
                    "visual.blank_frames",
                    "visual",
                    "Sampled frames contain visible non-uniform content.",
                    observed={"stddev": deviations},
                )
        except (MediaProbeError, OSError, ValueError) as exc:
            checks.unavailable(
                "visual.blank_frames",
                "visual",
                "Blank-frame inspection could not be performed.",
                observed=str(exc),
            )

        try:
            black = self.media_tools.detect_black(video_path)
            ratio = black.total_black_seconds / max(duration, 1e-6)
            if ratio > self.policy.black_frame_failure_ratio:
                checks.fail(
                    "visual.black_frames",
                    "visual",
                    "Most of the video is black; the scene is effectively unreadable.",
                    expected=f"<= {self.policy.black_frame_failure_ratio:.0%} black",
                    observed={"black_ratio": ratio, "intervals": len(black.intervals)},
                    retryable=True,
                )
            elif ratio > self.policy.black_frame_warning_ratio:
                checks.warn(
                    "visual.black_frames",
                    "visual",
                    "A large fraction of the video is black.",
                    expected=f"<= {self.policy.black_frame_warning_ratio:.0%} black",
                    observed={"black_ratio": ratio},
                )
            else:
                checks.pass_(
                    "visual.black_frames",
                    "visual",
                    "Black-frame coverage is within the configured limit.",
                    observed={"black_ratio": ratio},
                )
        except MediaProbeError as exc:
            checks.unavailable(
                "visual.black_frames",
                "visual",
                "Black-frame detection could not be performed.",
                observed=exc.message,
            )

        if scene.visual.allow_presenter:
            checks.fail(
                "visual.no_presenter",
                "visual",
                "Presenter footage is not allowed by the product contract.",
            )
        else:
            checks.pass_(
                "visual.no_presenter",
                "visual",
                "Scene contract prohibits presenter footage.",
            )

    def _text_checks(self, checks: _Checks, scene: SceneSpec) -> None:
        generation = scene.generation
        if generation is None:
            checks.unavailable(
                "text.accuracy",
                "text",
                "Text accuracy cannot be checked without generation metadata.",
            )
            return
        if generation.text_render_mode != TextRenderMode.DETERMINISTIC:
            checks.unavailable(
                "text.accuracy",
                "text",
                "Visible text/code/equation accuracy was not verified. No OCR or "
                "text inspector is configured for generative footage.",
                expected="deterministic text manifest or OCR",
                observed=generation.text_render_mode.value,
            )
            return
        if scene.media.manifest is None:
            checks.fail(
                "text.accuracy",
                "text",
                "Deterministic renderer did not provide a text manifest.",
            )
            return
        try:
            manifest = self.storage.read_json(scene.media.manifest.artifact_key)
        except StorageError as exc:
            checks.fail(
                "text.accuracy",
                "text",
                "Deterministic text manifest could not be read.",
                observed=exc.message,
            )
            return
        if not isinstance(manifest, dict):
            checks.fail(
                "text.accuracy",
                "text",
                "Deterministic text manifest has an unexpected JSON shape.",
                observed=type(manifest).__name__,
            )
            return
        rendered = manifest.get("rendered_texts")
        if not isinstance(rendered, list) or not all(
            isinstance(item, str) for item in rendered
        ):
            checks.fail(
                "text.accuracy",
                "text",
                "Deterministic text manifest has no valid rendered_texts list.",
                observed=type(rendered).__name__,
            )
            return
        expected = _normalize_texts(scene.expected_visible_texts)
        actual = _normalize_texts(rendered)
        if expected == actual:
            checks.pass_(
                "text.accuracy",
                "text",
                "Rendered deterministic text exactly matches the scene contract.",
                expected=expected,
                observed=actual,
            )
        else:
            checks.fail(
                "text.accuracy",
                "text",
                "Rendered deterministic text differs from the authoritative scene text.",
                expected=expected,
                observed=actual,
            )

    def _style_checks(
        self,
        checks: _Checks,
        scene: SceneSpec,
        expected_style: str | None,
    ) -> None:
        if expected_style is not None:
            if scene.visual.whiteboard_style == expected_style:
                checks.pass_(
                    "style.expected",
                    "style",
                    "Scene style metadata matches the job's required style.",
                    expected=expected_style,
                    observed=scene.visual.whiteboard_style,
                )
            else:
                checks.fail(
                    "style.expected",
                    "style",
                    "Scene style metadata conflicts with the job's required style.",
                    expected=expected_style,
                    observed=scene.visual.whiteboard_style,
                )
        else:
            checks.pass_(
                "style.expected",
                "style",
                "No job-level style requirement was supplied.",
                observed=scene.visual.whiteboard_style,
            )

        generation = scene.generation
        provider_style = (
            generation.provider_metadata.get("whiteboard_style")
            if generation is not None
            else None
        )
        if provider_style is None:
            checks.unavailable(
                "style.provider_metadata",
                "style",
                "Provider did not report whiteboard style metadata; pixel-level "
                "style verification is not implemented.",
                expected=scene.visual.whiteboard_style,
                observed=None,
            )
        elif provider_style == scene.visual.whiteboard_style:
            checks.pass_(
                "style.provider_metadata",
                "style",
                "Provider-reported whiteboard style matches the scene contract.",
                expected=scene.visual.whiteboard_style,
                observed=provider_style,
            )
        else:
            checks.fail(
                "style.provider_metadata",
                "style",
                "Provider-reported whiteboard style conflicts with the scene contract.",
                expected=scene.visual.whiteboard_style,
                observed=provider_style,
            )

        if generation is not None and generation.text_render_mode == TextRenderMode.DETERMINISTIC:
            if scene.media.manifest is None:
                checks.fail(
                    "style.background",
                    "style",
                    "Deterministic renderer manifest is missing; background style "
                    "cannot be verified.",
                )
            else:
                try:
                    manifest = self.storage.read_json(scene.media.manifest.artifact_key)
                    if not isinstance(manifest, dict):
                        checks.fail(
                            "style.background",
                            "style",
                            "Deterministic manifest has an unexpected JSON shape.",
                            observed=type(manifest).__name__,
                        )
                        return
                    background = manifest.get("background_color")
                    if background is None:
                        checks.unavailable(
                            "style.background",
                            "style",
                            "Deterministic manifest does not report a background color.",
                        )
                    elif str(background).upper() == scene.visual.background_color.upper():
                        checks.pass_(
                            "style.background",
                            "style",
                            "Rendered background color matches the scene contract.",
                            expected=scene.visual.background_color,
                            observed=background,
                        )
                    else:
                        checks.fail(
                            "style.background",
                            "style",
                            "Rendered background color conflicts with the scene contract.",
                            expected=scene.visual.background_color,
                            observed=background,
                        )
                except StorageError as exc:
                    checks.fail(
                        "style.background",
                        "style",
                        "Deterministic style manifest could not be read.",
                        observed=exc.message,
                    )
        else:
            checks.unavailable(
                "style.background",
                "style",
                "Pixel-level background/style verification is not implemented for "
                "generative footage.",
            )

    def _safety_checks(
        self,
        checks: _Checks,
        scene: SceneSpec,
        info: MediaInfo,
        video_path: Path,
    ) -> None:
        generation = scene.generation
        if generation is None or generation.narration_sha256 is None:
            checks.unavailable(
                "safety.narration_integrity",
                "safety",
                "Provider did not report a narration hash; narration integrity "
                "could not be independently verified.",
            )
        elif generation.narration_sha256 == scene.narration_sha256:
            checks.pass_(
                "safety.narration_integrity",
                "safety",
                "Provider-reported narration hash matches the scene narration.",
            )
        else:
            checks.fail(
                "safety.narration_integrity",
                "safety",
                "Provider-reported narration hash conflicts with the scene narration.",
                expected=scene.narration_sha256,
                observed=generation.narration_sha256,
            )

        checks.pass_(
            "safety.presenter_policy",
            "safety",
            "No presenter/avatar is permitted by the scene contract.",
        )
        checks.unavailable(
            "safety.source_grounding",
            "safety",
            "No source-grounded semantic accuracy evaluation is implemented; no "
            "score is fabricated.",
            expected="source-grounded evaluation",
            observed="not implemented",
        )

        try:
            stored_sha = file_sha256(video_path)
            expected_sha = scene.media.video.sha256 if scene.media.video else None
            if expected_sha is not None and stored_sha == expected_sha:
                checks.pass_(
                    "safety.video_integrity",
                    "safety",
                    "Stored video hash matches the recorded artifact metadata.",
                )
            elif expected_sha is not None:
                checks.fail(
                    "safety.video_integrity",
                    "safety",
                    "Stored video hash does not match the recorded artifact metadata.",
                    expected=expected_sha,
                    observed=stored_sha,
                    retryable=True,
                )
        except OSError as exc:
            checks.unavailable(
                "safety.video_integrity",
                "safety",
                "Video hash could not be recomputed.",
                observed=str(exc),
            )
