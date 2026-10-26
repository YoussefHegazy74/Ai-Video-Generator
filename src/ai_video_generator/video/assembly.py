"""Deterministic video assembly planning and FFmpeg rendering.

The renderer normalizes every clip to one video/audio format, adds optional
intro/outro cards and scene/progress overlays, and concatenates in explicit
``scene_index`` order. Supported transitions are hard cuts and non-overlapping
fade-through-black; overlapping crossfades are intentionally not offered because
they trim clip edges and can truncate narration.
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_video_generator.core.constants import (
    ASSEMBLY_SCHEMA_VERSION,
    TransitionStyle,
)
from ai_video_generator.core.exceptions import (
    AssemblyInputError,
    AssemblyRenderError,
    MediaProbeError,
)
from ai_video_generator.video.continuity import validate_scene_set_structure
from ai_video_generator.video.media_probe import FFmpegTools, MediaInfo, file_sha256
from ai_video_generator.video.scene_contract import SceneSet, utc_now
from ai_video_generator.video.text_rendering import (
    contrasting_text_color,
    draw_grid,
    draw_wrapped,
    load_font,
    parse_color,
    wrap_text,
)

ProgressCallback = Callable[[str, float], None]


class AssemblyOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_intro: bool = True
    include_outro: bool = True
    intro_title: str | None = None
    intro_subtitle: str | None = None
    outro_title: str | None = None
    outro_subtitle: str | None = None
    intro_duration_seconds: float = Field(default=2.5, gt=0, le=30)
    outro_duration_seconds: float = Field(default=2.5, gt=0, le=30)
    transition: TransitionStyle = TransitionStyle.FADE_BLACK
    transition_duration_seconds: float = Field(default=0.35, ge=0, le=2.0)
    width: int = Field(default=1280, ge=160)
    height: int = Field(default=720, ge=90)
    fps: int = Field(default=24, ge=1, le=120)
    video_codec: Literal["libx264"] = "libx264"
    audio_codec: Literal["aac"] = "aac"
    sample_rate: Literal[44100, 48000] = 48000
    channels: Literal[2] = 2
    output_container: Literal["mp4", "mov"] = "mp4"
    show_scene_titles: bool = True
    show_progress: bool = True
    audio_fade_seconds: float = Field(default=0.0, ge=0, le=1.0)
    duration_tolerance_seconds: float = Field(default=1.5, ge=0)
    render_timeout_seconds: float = Field(default=3600.0, gt=0)
    crf: int = Field(default=20, ge=0, le=51)
    preset: str = "veryfast"

    @model_validator(mode="after")
    def _validate_transition(self) -> "AssemblyOptions":
        if (
            self.transition == TransitionStyle.FADE_BLACK
            and self.transition_duration_seconds <= 0
        ):
            raise ValueError("fade_black requires a positive transition duration")
        if self.transition == TransitionStyle.FADE_BLACK:
            shortest = min(self.intro_duration_seconds, self.outro_duration_seconds)
            if self.transition_duration_seconds > shortest / 2:
                raise ValueError(
                    "transition_duration_seconds must not exceed half of the "
                    "shortest intro/outro duration"
                )
        return self


class AssemblyEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    kind: Literal["intro", "scene", "outro"]
    scene_id: str | None = None
    scene_index: int | None = None
    title: str
    section_title: str | None = None
    source_path: Path | None = Field(default=None, exclude=True)
    media_sha256: str | None = None
    duration_seconds: float = Field(gt=0)
    has_audio: bool = True


class AssemblyPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    job_id: str
    course_title: str | None = None
    expected_style: str | None = None
    entries: list[AssemblyEntry] = Field(min_length=1)
    expected_duration_seconds: float = Field(gt=0)
    options: AssemblyOptions
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def scene_entries(self) -> list[AssemblyEntry]:
        return [entry for entry in self.entries if entry.kind == "scene"]

    @property
    def scene_order(self) -> list[str]:
        return [
            entry.scene_id
            for entry in self.scene_entries
            if entry.scene_id is not None
        ]


class SceneAssemblyArtifact(BaseModel):
    scene_id: str
    scene_index: int
    artifact_key: str
    sha256: str
    duration_seconds: float


class AssemblyVerification(BaseModel):
    output_exists: bool
    output_non_empty: bool
    duration_seconds: float
    expected_duration_seconds: float
    duration_within_tolerance: bool
    video_stream_present: bool
    audio_stream_present: bool
    width: int | None
    height: int | None
    fps: float | None
    video_codec: str | None
    audio_codec: str | None
    scene_order: list[str]
    audio_not_silent: bool | None
    notes: list[str] = Field(default_factory=list)


class AssemblyManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = ASSEMBLY_SCHEMA_VERSION
    job_id: str
    output_key: str
    output_sha256: str
    size_bytes: int = Field(ge=0)
    duration_seconds: float = Field(gt=0)
    expected_duration_seconds: float = Field(gt=0)
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    container: str | None = None
    scene_order: list[str]
    scenes: list[SceneAssemblyArtifact]
    intro_included: bool
    outro_included: bool
    transition: TransitionStyle
    transition_duration_seconds: float
    options: AssemblyOptions
    verification: AssemblyVerification
    created_at: datetime = Field(default_factory=utc_now)


def _effective_fade(options: AssemblyOptions, duration: float) -> float:
    if options.transition != TransitionStyle.FADE_BLACK:
        return 0.0
    return min(options.transition_duration_seconds, max(0.05, duration / 3))


class FFmpegAssembler:
    def __init__(
        self,
        media_tools: FFmpegTools,
        options: AssemblyOptions,
    ) -> None:
        self.media_tools = media_tools
        self.options = options

    def build_plan(
        self,
        scene_set: SceneSet,
        job_id: str,
        source_paths: dict[str, Path],
    ) -> AssemblyPlan:
        scene_set.require_assembly_ready()
        continuity = validate_scene_set_structure(scene_set)
        if continuity.is_blocked:
            raise AssemblyInputError(
                "Scene set failed structural/style continuity checks",
                details={
                    "failures": [
                        check.model_dump(mode="json")
                        for check in continuity.blocking_failures
                    ]
                },
            )

        ordered = scene_set.ordered_scenes
        entries: list[AssemblyEntry] = []
        if self.options.include_intro:
            intro_title = (
                self.options.intro_title
                or scene_set.course_title
                or "Educational Lesson"
            )
            intro_subtitle = self.options.intro_subtitle
            if intro_subtitle is None:
                intro_subtitle = f"{len(ordered)} scene{'s' if len(ordered) != 1 else ''}"
            entries.append(
                AssemblyEntry(
                    kind="intro",
                    title=intro_title,
                    section_title=intro_subtitle,
                    duration_seconds=self.options.intro_duration_seconds,
                    has_audio=True,
                )
            )

        for scene in ordered:
            if scene.scene_id not in source_paths:
                raise AssemblyInputError(
                    f"No local media path was supplied for scene {scene.scene_id!r}",
                    details={"scene_id": scene.scene_id},
                )
            source = Path(source_paths[scene.scene_id])
            if not source.is_file() or source.stat().st_size <= 0:
                raise AssemblyInputError(
                    f"Scene media is missing or empty: {source}",
                    details={"scene_id": scene.scene_id, "path": str(source)},
                )
            try:
                info = self.media_tools.probe(source)
            except MediaProbeError as exc:
                raise AssemblyInputError(
                    f"Scene media is unreadable: {scene.scene_id}",
                    details={"scene_id": scene.scene_id, "error": exc.message},
                    cause=exc,
                ) from exc
            if info.video is None:
                raise AssemblyInputError(
                    f"Scene media has no video stream: {scene.scene_id}",
                    details={"scene_id": scene.scene_id},
                )
            if self.options.include_intro or self.options.include_outro:
                if info.audio is None:
                    raise AssemblyInputError(
                        f"Scene media has no narration audio: {scene.scene_id}",
                        details={"scene_id": scene.scene_id},
                    )
            entries.append(
                AssemblyEntry(
                    kind="scene",
                    scene_id=scene.scene_id,
                    scene_index=scene.scene_index,
                    title=scene.title,
                    section_title=scene.section_title,
                    source_path=source,
                    media_sha256=file_sha256(source),
                    duration_seconds=info.duration_seconds,
                    has_audio=info.audio is not None,
                )
            )

        if self.options.include_outro:
            outro_title = self.options.outro_title or "Lesson complete"
            outro_subtitle = self.options.outro_subtitle
            if outro_subtitle is None and scene_set.course_title:
                outro_subtitle = scene_set.course_title
            entries.append(
                AssemblyEntry(
                    kind="outro",
                    title=outro_title,
                    section_title=outro_subtitle,
                    duration_seconds=self.options.outro_duration_seconds,
                    has_audio=True,
                )
            )

        expected_duration = sum(entry.duration_seconds for entry in entries)
        return AssemblyPlan(
            job_id=job_id,
            course_title=scene_set.course_title,
            expected_style=scene_set.expected_style,
            entries=entries,
            expected_duration_seconds=expected_duration,
            options=self.options,
        )

    def render(
        self,
        plan: AssemblyPlan,
        work_dir: Path,
        output_path: Path,
        *,
        progress: ProgressCallback | None = None,
    ) -> Path:
        work_dir = Path(work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        total_entries = len(plan.entries)
        normalized: list[Path] = []
        scene_total = len(plan.scene_entries)

        for position, entry in enumerate(plan.entries):
            if entry.kind == "intro":
                if progress:
                    progress("rendering_intro", 0.02)
                path = work_dir / "normalized-000-intro.mp4"
                self._render_title_card(entry, path, "intro")
            elif entry.kind == "outro":
                if progress:
                    progress("rendering_outro", 0.88)
                path = work_dir / f"normalized-{position:03d}-outro.mp4"
                self._render_title_card(entry, path, "outro")
            else:
                if progress:
                    progress(
                        "assembling_scenes",
                        0.10 + 0.75 * ((entry.scene_index or 0) / max(1, scene_total)),
                    )
                path = work_dir / f"normalized-{position:03d}-scene.mp4"
                self._normalize_scene(entry, path, scene_total)
            self._verify_normalized(path, entry.duration_seconds)
            normalized.append(path)

        if progress:
            progress("concatenating", 0.92)
        self._concat(normalized, output_path, work_dir)
        if not output_path.is_file() or output_path.stat().st_size <= 0:
            raise AssemblyRenderError("Assembly did not produce a non-empty output file")
        if progress:
            progress("finalizing", 0.98)
        return output_path

    # ------------------------------------------------------------- normalization

    def _normalize_scene(
        self, entry: AssemblyEntry, output_path: Path, scene_total: int
    ) -> None:
        assert entry.source_path is not None
        info = self.media_tools.probe(entry.source_path)
        duration = info.duration_seconds
        fade = _effective_fade(self.options, duration)
        overlay_path: Path | None = None
        if self.options.show_scene_titles or self.options.show_progress:
            overlay_path = output_path.with_name(output_path.stem + "-overlay.png")
            self._create_scene_overlay(entry, overlay_path, scene_total)

        filter_parts = [
            f"[0:v]scale={self.options.width}:{self.options.height}:"
            "force_original_aspect_ratio=decrease,"
            f"pad={self.options.width}:{self.options.height}:(ow-iw)/2:(oh-ih)/2:"
            "color=#F8FAFC,setsar=1,"
            f"fps={self.options.fps},format=yuv420p[base]"
        ]
        video_label = "base"
        input_index = 1
        if overlay_path is not None:
            filter_parts.append(f"[{input_index}:v]format=rgba[overlay]")
            filter_parts.append(
                f"[{video_label}][overlay]overlay=0:0:format=auto[composited]"
            )
            video_label = "composited"
        fade_filters = []
        if fade > 0:
            fade_filters.append(f"fade=t=in:st=0:d={fade:.3f}")
            fade_filters.append(
                f"fade=t=out:st={max(0.0, duration - fade):.3f}:d={fade:.3f}"
            )
        video_chain = ",".join(fade_filters + ["format=yuv420p"])
        filter_parts.append(f"[{video_label}]{video_chain}[v]")

        audio_chain = [
            f"aresample={self.options.sample_rate}",
            f"aformat=sample_fmts=fltp:channel_layouts=stereo",
            f"apad=whole_dur={duration:.3f}",
            f"atrim=0:{duration:.3f}",
        ]
        if self.options.audio_fade_seconds > 0:
            audio_chain.extend(
                [
                    f"afade=t=in:st=0:d={self.options.audio_fade_seconds:.3f}",
                    f"afade=t=out:st={max(0.0, duration - self.options.audio_fade_seconds):.3f}:"
                    f"d={self.options.audio_fade_seconds:.3f}",
                ]
            )
        filter_parts.append(f"[0:a]{','.join(audio_chain)}[a]")
        filter_complex = ";".join(filter_parts)

        command = [
            self.media_tools.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(entry.source_path),
        ]
        if overlay_path is not None:
            command.extend(["-i", str(overlay_path)])
        command.extend(
            [
                "-filter_complex",
                filter_complex,
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-t",
                f"{duration:.3f}",
                "-c:v",
                self.options.video_codec,
                "-preset",
                self.options.preset,
                "-crf",
                str(self.options.crf),
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(self.options.fps),
                "-c:a",
                self.options.audio_codec,
                "-b:a",
                "192k",
                "-ar",
                str(self.options.sample_rate),
                "-ac",
                str(self.options.channels),
                "-movflags",
                "+faststart",
                "-y",
                str(output_path),
            ]
        )
        result = self.media_tools.run(
            command,
            timeout_seconds=self.options.render_timeout_seconds,
            check=False,
        )
        if result.returncode != 0:
            raise AssemblyRenderError(
                f"FFmpeg failed while normalizing scene {entry.scene_id}",
                details={
                    "scene_id": entry.scene_id,
                    "exit_code": result.returncode,
                    "stderr_tail": (result.stderr or "").strip().splitlines()[-12:],
                },
            )

    def _render_title_card(
        self, entry: AssemblyEntry, output_path: Path, kind: str
    ) -> None:
        duration = entry.duration_seconds
        image_path = output_path.with_name(output_path.stem + "-card.png")
        self._create_title_card_image(entry, image_path)
        fade = _effective_fade(self.options, duration)
        fade_filters = []
        if fade > 0:
            fade_filters.append(f"fade=t=in:st=0:d={fade:.3f}")
            fade_filters.append(
                f"fade=t=out:st={max(0.0, duration - fade):.3f}:d={fade:.3f}"
            )
        video_filter = ",".join(
            fade_filters
            + [
                f"fps={self.options.fps}",
                "format=yuv420p",
            ]
        )
        command = [
            self.media_tools.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=channel_layout=stereo:sample_rate={self.options.sample_rate}",
            "-filter_complex",
            f"[0:v]{video_filter}[v];[1:a]atrim=0:{duration:.3f}[a]",
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-t",
            f"{duration:.3f}",
            "-c:v",
            self.options.video_codec,
            "-preset",
            self.options.preset,
            "-crf",
            str(self.options.crf),
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(self.options.fps),
            "-c:a",
            self.options.audio_codec,
            "-b:a",
            "192k",
            "-ar",
            str(self.options.sample_rate),
            "-ac",
            str(self.options.channels),
            "-movflags",
            "+faststart",
            "-y",
            str(output_path),
        ]
        result = self.media_tools.run(
            command,
            timeout_seconds=self.options.render_timeout_seconds,
            check=False,
        )
        if result.returncode != 0:
            raise AssemblyRenderError(
                f"FFmpeg failed while rendering the {kind} title card",
                details={
                    "kind": kind,
                    "exit_code": result.returncode,
                    "stderr_tail": (result.stderr or "").strip().splitlines()[-12:],
                },
            )

    def _create_title_card_image(self, entry: AssemblyEntry, path: Path) -> None:
        width, height = self.options.width, self.options.height
        background = parse_color("#F8FAFC")
        accent = parse_color("#2563EB")
        text_color = contrasting_text_color("#F8FAFC")
        grid = tuple(int(channel * 0.93 + 255 * 0.07) for channel in background)
        image = Image.new("RGB", (width, height), background)
        draw = ImageDraw.Draw(image)
        draw_grid(draw, (width, height), color=grid, spacing=max(24, width // 26))
        margin = max(40, width // 16)
        draw.rectangle(
            [(margin, height // 3 - 20), (margin + 10, height // 3 + 120)],
            fill=accent,
        )
        title_font = load_font(max(30, width // 22), "bold")
        subtitle_font = load_font(max(18, width // 48), "regular")
        draw_wrapped(
            draw,
            entry.title,
            (margin + 38, height // 3 - 24),
            title_font,
            width - 2 * margin - 38,
            text_color,
            line_spacing=12,
            max_lines=3,
        )
        if entry.section_title:
            draw_wrapped(
                draw,
                entry.section_title,
                (margin + 38, height // 3 + 130),
                subtitle_font,
                width - 2 * margin - 38,
                accent,
                line_spacing=8,
                max_lines=2,
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, format="PNG")

    def _create_scene_overlay(
        self, entry: AssemblyEntry, path: Path, scene_total: int
    ) -> None:
        width, height = self.options.width, self.options.height
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        bar_height = max(54, height // 10)
        top = height - bar_height
        draw.rectangle([(0, top), (width, height)], fill=(15, 23, 42, 185))
        font = load_font(max(14, width // 64), "regular")
        small = load_font(max(12, width // 80), "regular")
        margin = max(20, width // 48)
        text_y = top + max(8, bar_height // 5)
        if self.options.show_scene_titles:
            label = entry.title
            if entry.section_title:
                label = f"{entry.section_title} · {label}"
            max_width = width - 2 * margin - max(150, width // 6)
            lines = wrap_text(draw, label, font, max_width)
            if len(lines) > 1:
                lines = lines[:1]
                lines[0] = lines[0].rstrip() + "…"
            draw.text((margin, text_y), lines[0] if lines else "", font=font, fill=(248, 250, 252))
        if self.options.show_progress:
            progress_text = (
                f"Scene {(entry.scene_index or 0) + 1} of {scene_total}"
            )
            text_width = draw.textlength(progress_text, font=small)
            draw.text(
                (width - margin - text_width, text_y + 4),
                progress_text,
                font=small,
                fill=(203, 213, 225),
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        overlay.save(path, format="PNG")

    def _verify_normalized(self, path: Path, expected_duration: float) -> None:
        if not path.is_file() or path.stat().st_size <= 0:
            raise AssemblyRenderError(f"Normalized clip is missing or empty: {path}")
        try:
            info = self.media_tools.probe(path)
        except MediaProbeError as exc:
            raise AssemblyRenderError(
                f"Normalized clip is unreadable: {path}",
                details={"error": exc.message},
                cause=exc,
            ) from exc
        if info.video is None:
            raise AssemblyRenderError(f"Normalized clip has no video stream: {path}")
        if abs(info.duration_seconds - expected_duration) > max(
            0.5, expected_duration * 0.1
        ):
            raise AssemblyRenderError(
                "Normalized clip duration drifted beyond tolerance",
                details={
                    "path": str(path),
                    "expected": expected_duration,
                    "actual": info.duration_seconds,
                },
            )

    def _concat(self, clips: list[Path], output_path: Path, work_dir: Path) -> None:
        if not clips:
            raise AssemblyRenderError("No clips were prepared for concatenation")
        graph_lines = []
        labels = []
        for index in range(len(clips)):
            labels.append(f"[{index}:v][{index}:a]")
        graph_lines.append(
            "".join(labels) + f"concat=n={len(clips)}:v=1:a=1[v][a]"
        )
        graph_path = work_dir / "concat-filter.txt"
        graph_path.write_text("\n".join(graph_lines) + "\n", encoding="utf-8")
        command = [self.media_tools.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostdin"]
        for clip in clips:
            command.extend(["-i", str(clip)])
        command.extend(
            [
                "-filter_complex_script",
                str(graph_path),
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-c:v",
                self.options.video_codec,
                "-preset",
                self.options.preset,
                "-crf",
                str(self.options.crf),
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(self.options.fps),
                "-c:a",
                self.options.audio_codec,
                "-b:a",
                "192k",
                "-ar",
                str(self.options.sample_rate),
                "-ac",
                str(self.options.channels),
                "-movflags",
                "+faststart",
                "-y",
                str(output_path),
            ]
        )
        result = self.media_tools.run(
            command,
            timeout_seconds=self.options.render_timeout_seconds,
            check=False,
        )
        if result.returncode != 0:
            raise AssemblyRenderError(
                "FFmpeg failed while concatenating the final video",
                details={
                    "exit_code": result.returncode,
                    "stderr_tail": (result.stderr or "").strip().splitlines()[-12:],
                },
            )
