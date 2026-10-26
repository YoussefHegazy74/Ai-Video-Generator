"""Safe FFprobe/FFmpeg wrappers used by generation, validation, and assembly.

All subprocesses use argument lists (never shell strings), explicit timeouts, and
captured diagnostics. File paths are passed as separate arguments.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ai_video_generator.core.exceptions import (
    MediaProbeError,
    MediaToolUnavailableError,
)

_MEAN_VOLUME_RE = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")
_MAX_VOLUME_RE = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")
_BLACK_START_RE = re.compile(r"black_start:(\d+(?:\.\d+)?)")
_BLACK_END_RE = re.compile(r"black_end:(\d+(?:\.\d+)?)")


def _fraction_to_float(value: str | None) -> float | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    try:
        if "/" in value:
            numerator, denominator = value.split("/", 1)
            denominator_value = float(denominator)
            if denominator_value == 0:
                return None
            return float(numerator) / denominator_value
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: object) -> float | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _to_int(value: object) -> int | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class MediaStreamInfo:
    index: int
    codec_type: str
    codec_name: str
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    start_time_seconds: float | None = None
    bit_rate: int | None = None
    nb_frames: int | None = None


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    size_bytes: int
    format_name: str
    duration_seconds: float
    video: MediaStreamInfo | None
    audio: MediaStreamInfo | None
    video_streams: int
    audio_streams: int
    stream_durations_available: bool

    @property
    def container_names(self) -> set[str]:
        return {name.strip().lower() for name in self.format_name.split(",") if name}

    @property
    def has_audio(self) -> bool:
        return self.audio is not None

    @property
    def video_duration_seconds(self) -> float | None:
        if self.video is None:
            return None
        return self.video.duration_seconds or self.duration_seconds

    @property
    def audio_duration_seconds(self) -> float | None:
        if self.audio is None:
            return None
        return self.audio.duration_seconds or self.duration_seconds

    @property
    def audio_video_duration_delta_seconds(self) -> float | None:
        if self.video_duration_seconds is None or self.audio_duration_seconds is None:
            return None
        return abs(self.video_duration_seconds - self.audio_duration_seconds)

    @property
    def audio_video_start_delta_seconds(self) -> float | None:
        if self.video is None or self.audio is None:
            return None
        if (
            self.video.start_time_seconds is None
            or self.audio.start_time_seconds is None
        ):
            return None
        return abs(self.video.start_time_seconds - self.audio.start_time_seconds)


@dataclass(frozen=True)
class AudioLevelInfo:
    mean_db: float
    max_db: float


@dataclass(frozen=True)
class BlackInterval:
    start_seconds: float
    end_seconds: float

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)


@dataclass(frozen=True)
class BlackDetectionInfo:
    intervals: tuple[BlackInterval, ...]
    total_black_seconds: float


def file_sha256(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


class FFmpegTools:
    """Thin, safe wrapper around the local FFmpeg/FFprobe binaries."""

    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        *,
        timeout_seconds: float = 60.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.timeout_seconds = timeout_seconds

    @property
    def _creationflags(self) -> int:
        if sys.platform == "win32":
            return getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return 0

    def ensure_available(self) -> None:
        missing: list[str] = []
        for name, configured in (
            ("ffmpeg", self.ffmpeg_path),
            ("ffprobe", self.ffprobe_path),
        ):
            resolved = shutil.which(configured)
            if resolved is None and not Path(configured).is_file():
                missing.append(name)
        if missing:
            raise MediaToolUnavailableError(
                "Required media tool(s) not found: "
                + ", ".join(missing)
                + ". Install FFmpeg and ensure ffmpeg/ffprobe are on PATH, or "
                "configure AIVG_MEDIA_FFMPEG_PATH / AIVG_MEDIA_FFPROBE_PATH.",
                details={"missing": missing},
            )

    def run(
        self,
        args: Sequence[str],
        *,
        timeout_seconds: float | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        timeout = timeout_seconds or self.timeout_seconds
        command = [str(item) for item in args]
        try:
            result = subprocess.run(
                command,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=self._creationflags,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise MediaProbeError(
                f"Media command timed out after {timeout:.1f}s: {command[0]}",
                details={"tool": command[0], "timeout_seconds": timeout},
                cause=exc,
            ) from exc
        except FileNotFoundError as exc:
            raise MediaToolUnavailableError(
                f"Media binary not found: {command[0]}",
                details={"binary": command[0]},
                cause=exc,
            ) from exc
        if check and result.returncode != 0:
            stderr_tail = (result.stderr or "").strip().splitlines()[-12:]
            raise MediaProbeError(
                f"Media command failed with exit code {result.returncode}: "
                f"{command[0]}",
                details={
                    "tool": command[0],
                    "exit_code": result.returncode,
                    "stderr_tail": stderr_tail,
                },
            )
        return result

    def probe(self, path: Path) -> MediaInfo:
        path = Path(path)
        if not path.exists():
            raise MediaProbeError(
                f"Media file does not exist: {path}",
                details={"path": str(path)},
            )
        if not path.is_file():
            raise MediaProbeError(
                f"Media path is not a file: {path}",
                details={"path": str(path)},
            )
        size_bytes = path.stat().st_size
        if size_bytes <= 0:
            raise MediaProbeError(
                f"Media file is empty: {path}",
                details={"path": str(path), "size_bytes": size_bytes},
            )

        result = self.run(
            [
                self.ffprobe_path,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ]
        )
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise MediaProbeError(
                "FFprobe returned invalid JSON",
                details={"path": str(path)},
                cause=exc,
            ) from exc

        streams_payload = payload.get("streams") or []
        if not isinstance(streams_payload, list) or not streams_payload:
            raise MediaProbeError(
                "Media file contains no readable streams",
                details={"path": str(path)},
            )

        streams: list[MediaStreamInfo] = []
        for stream in streams_payload:
            if not isinstance(stream, dict):
                continue
            streams.append(
                MediaStreamInfo(
                    index=_to_int(stream.get("index")) or 0,
                    codec_type=str(stream.get("codec_type") or "unknown"),
                    codec_name=str(stream.get("codec_name") or "unknown"),
                    duration_seconds=_to_float(stream.get("duration")),
                    width=_to_int(stream.get("width")),
                    height=_to_int(stream.get("height")),
                    fps=_fraction_to_float(
                        str(stream.get("avg_frame_rate") or stream.get("r_frame_rate"))
                    ),
                    sample_rate=_to_int(stream.get("sample_rate")),
                    channels=_to_int(stream.get("channels")),
                    start_time_seconds=_to_float(stream.get("start_time")),
                    bit_rate=_to_int(stream.get("bit_rate")),
                    nb_frames=_to_int(stream.get("nb_frames")),
                )
            )

        format_payload = payload.get("format") or {}
        duration = _to_float(format_payload.get("duration"))
        if duration is None:
            candidates = [
                stream.duration_seconds
                for stream in streams
                if stream.duration_seconds is not None
            ]
            duration = max(candidates) if candidates else None
        if duration is None or duration <= 0:
            raise MediaProbeError(
                "Media duration could not be determined",
                details={"path": str(path)},
            )

        video_streams = [s for s in streams if s.codec_type == "video"]
        audio_streams = [s for s in streams if s.codec_type == "audio"]
        stream_durations_available = bool(video_streams and audio_streams) and all(
            stream.duration_seconds is not None
            for stream in (video_streams[0], audio_streams[0])
        )
        return MediaInfo(
            path=path,
            size_bytes=size_bytes,
            format_name=str(format_payload.get("format_name") or "unknown"),
            duration_seconds=duration,
            video=video_streams[0] if video_streams else None,
            audio=audio_streams[0] if audio_streams else None,
            video_streams=len(video_streams),
            audio_streams=len(audio_streams),
            stream_durations_available=stream_durations_available,
        )

    def extract_frame(
        self,
        path: Path,
        timestamp_seconds: float,
        output_path: Path,
        *,
        timeout_seconds: float | None = None,
    ) -> Path:
        if timestamp_seconds < 0:
            raise ValueError("timestamp_seconds must be non-negative")
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self.run(
            [
                self.ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-ss",
                f"{timestamp_seconds:.3f}",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-y",
                str(output_path),
            ],
            timeout_seconds=timeout_seconds,
        )
        if not output_path.exists() or output_path.stat().st_size <= 0:
            raise MediaProbeError(
                "FFmpeg did not produce a frame image",
                details={"path": str(path), "timestamp": timestamp_seconds},
            )
        return output_path

    def measure_audio_level(
        self,
        path: Path,
        *,
        timeout_seconds: float | None = None,
    ) -> AudioLevelInfo:
        result = self.run(
            [
                self.ffmpeg_path,
                "-hide_banner",
                "-nostdin",
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-vn",
                "-af",
                "volumedetect",
                "-f",
                "null",
                "-",
            ],
            timeout_seconds=timeout_seconds,
            check=False,
        )
        stderr = result.stderr or ""
        mean_match = _MEAN_VOLUME_RE.search(stderr)
        max_match = _MAX_VOLUME_RE.search(stderr)
        if result.returncode != 0 or mean_match is None or max_match is None:
            raise MediaProbeError(
                "Audio level could not be measured",
                details={
                    "path": str(path),
                    "exit_code": result.returncode,
                    "stderr_tail": stderr.strip().splitlines()[-8:],
                },
            )
        return AudioLevelInfo(
            mean_db=float(mean_match.group(1)),
            max_db=float(max_match.group(1)),
        )

    def detect_black(
        self,
        path: Path,
        *,
        min_duration_seconds: float = 0.1,
        pixel_threshold: float = 0.10,
        timeout_seconds: float | None = None,
    ) -> BlackDetectionInfo:
        if not 0 <= pixel_threshold <= 1:
            raise ValueError("pixel_threshold must be between 0 and 1")
        result = self.run(
            [
                self.ffmpeg_path,
                "-hide_banner",
                "-nostdin",
                "-i",
                str(path),
                "-vf",
                f"blackdetect=d={min_duration_seconds:.3f}:pix_th={pixel_threshold:.3f}",
                "-an",
                "-f",
                "null",
                "-",
            ],
            timeout_seconds=timeout_seconds,
            check=False,
        )
        stderr = result.stderr or ""
        if result.returncode != 0:
            raise MediaProbeError(
                "Black-frame detection failed",
                details={
                    "path": str(path),
                    "exit_code": result.returncode,
                    "stderr_tail": stderr.strip().splitlines()[-8:],
                },
            )
        starts = [float(match) for match in _BLACK_START_RE.findall(stderr)]
        ends = [float(match) for match in _BLACK_END_RE.findall(stderr)]
        intervals = tuple(
            BlackInterval(start_seconds=start, end_seconds=end)
            for start, end in zip(starts, ends)
        )
        return BlackDetectionInfo(
            intervals=intervals,
            total_black_seconds=sum(interval.duration_seconds for interval in intervals),
        )
