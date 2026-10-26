"""Deterministic local whiteboard renderer.

This is deliberately not an AI video model. It renders the authoritative scene
text with Pillow, synthesizes local narration, and muxes both with FFmpeg. That
makes text/code/equation rendering exact and testable while preserving the
whiteboard style and synchronized audio/video.
"""

from __future__ import annotations

import json
import math
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Iterator

from PIL import Image, ImageDraw

from ai_video_generator.core.constants import TextRenderMode
from ai_video_generator.core.exceptions import (
    MediaProbeError,
    MediaToolUnavailableError,
    ProviderCapabilityError,
    ProviderOutputError,
    ProviderTimeoutError,
    TtsUnavailableError,
)
from ai_video_generator.integrations.audiovisual_generation_provider import (
    DurationSelection,
    GeneratedClip,
    GenerationRequest,
    ProviderCapabilities,
)
from ai_video_generator.integrations.narration_synthesizer import (
    NarrationSynthesizer,
)
from ai_video_generator.video.media_probe import FFmpegTools
from ai_video_generator.video.scene_contract import SceneSpec
from ai_video_generator.video.text_rendering import (
    contrasting_text_color,
    draw_grid,
    draw_wrapped,
    load_font,
    parse_color,
    wrap_text,
)

_RENDERER_VERSION = "deterministic-whiteboard-1"


class DeterministicWhiteboardProvider:
    capabilities = ProviderCapabilities(
        provider_id="deterministic_whiteboard",
        display_name="Deterministic Whiteboard Renderer (local)",
        generates_synchronized_audio=True,
        supports_duration_control=True,
        supported_durations_seconds=None,
        min_duration_seconds=1.0,
        max_duration_seconds=120.0,
        supports_separate_audio=False,
        supports_idempotency_key=True,
        supports_cancellation=False,
        output_container="mp4",
        text_render_mode=TextRenderMode.DETERMINISTIC,
        requires_credentials=False,
        is_local=True,
        notes=[
            "Not an AI generator. Renders authoritative text deterministically.",
            "Narration is synthesized locally; audio duration determines measured "
            "clip duration.",
            "Supports progressive whiteboard reveals and a visible cursor.",
        ],
    )

    def __init__(
        self,
        media_tools: FFmpegTools,
        narrator: NarrationSynthesizer,
        *,
        width: int = 1280,
        height: int = 720,
        fps: int = 24,
        preset: str = "veryfast",
        crf: int = 20,
    ) -> None:
        if width < 160 or height < 90:
            raise ValueError("Whiteboard dimensions are too small")
        if fps < 1:
            raise ValueError("fps must be positive")
        if not 0 <= crf <= 51:
            raise ValueError("crf must be between 0 and 51")
        self.media_tools = media_tools
        self.narrator = narrator
        self.width = width
        self.height = height
        self.fps = fps
        self.preset = preset
        self.crf = crf

    def check_ready(self) -> None:
        self.media_tools.ensure_available()
        if not self.narrator.available():
            raise ProviderCapabilityError(
                "No local narration synthesizer is available. The deterministic "
                "whiteboard provider cannot generate narration audio.",
                provider=self.capabilities.provider_id,
            )
        if self.narrator.name == "auto_local" and getattr(
            self.narrator, "selected_name", "missing"
        ) is None:
            raise ProviderCapabilityError(
                "No local narration synthesizer is available. Install Windows "
                "System.Speech support or use an FFmpeg build with flite.",
                provider=self.capabilities.provider_id,
            )

    def resolve_duration(self, requested_seconds: float) -> DurationSelection:
        if requested_seconds <= 0:
            raise ProviderCapabilityError(
                "Requested duration must be positive",
                provider=self.capabilities.provider_id,
            )
        minimum = self.capabilities.min_duration_seconds or 1.0
        maximum = self.capabilities.max_duration_seconds or 120.0
        if not minimum <= requested_seconds <= maximum:
            raise ProviderCapabilityError(
                f"Requested duration {requested_seconds:.2f}s is outside the local "
                f"renderer's supported range {minimum:.2f}-{maximum:.2f}s",
                provider=self.capabilities.provider_id,
                details={
                    "requested_seconds": requested_seconds,
                    "min_seconds": minimum,
                    "max_seconds": maximum,
                },
            )
        return DurationSelection(
            requested_seconds=requested_seconds,
            selected_seconds=requested_seconds,
            duration_control=True,
            note=(
                "The local renderer uses the measured narration WAV duration plus a "
                "short audio tail as the final clip duration; the target guides "
                "narration pacing but is not copied into metadata as actual duration."
            ),
        )

    def generate(self, request: GenerationRequest) -> GeneratedClip:
        self.check_ready()
        deadline = time.monotonic() + request.timeout_seconds
        scene = request.scene
        output_dir = Path(request.output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        narration_path = output_dir / "narration.wav"
        silent_path = output_dir / "silent-video.mp4"
        output_path = output_dir / f"{scene.scene_id}.mp4"
        manifest_path = output_dir / f"{scene.scene_id}.manifest.json"

        remaining = max(1.0, deadline - time.monotonic())
        try:
            self.narrator.synthesize(
                scene.narration,
                narration_path,
                timeout_seconds=remaining,
            )
        except TtsUnavailableError as exc:
            raise ProviderCapabilityError(
                "Local narration synthesis failed; refusing to generate a scene "
                "with missing narration.",
                provider=self.capabilities.provider_id,
                details=exc.details,
                cause=exc,
            ) from exc

        try:
            narration_info = self.media_tools.probe(narration_path)
        except MediaProbeError as exc:
            raise ProviderOutputError(
                "Narration audio could not be measured after synthesis.",
                provider=self.capabilities.provider_id,
                retryable=True,
                details=exc.details,
                cause=exc,
            ) from exc
        audio_duration = narration_info.duration_seconds
        if audio_duration <= 0:
            raise ProviderOutputError(
                "Narration audio has no measurable duration.",
                provider=self.capabilities.provider_id,
                retryable=True,
            )
        video_duration = audio_duration + 0.25
        maximum = self.capabilities.max_duration_seconds or 120.0
        if video_duration > maximum:
            raise ProviderCapabilityError(
                f"Narration is {audio_duration:.2f}s long, exceeding the local "
                f"renderer maximum of {maximum:.2f}s.",
                provider=self.capabilities.provider_id,
                details={"audio_duration_seconds": audio_duration},
            )

        frame_count = max(1, math.ceil(video_duration * self.fps))
        self._encode_frames(
            self._frames(scene, frame_count, video_duration),
            silent_path,
            deadline=deadline,
            request_timeout=request.timeout_seconds,
        )
        self._mux_audio(
            silent_path,
            narration_path,
            output_path,
            video_duration,
            deadline,
            request.timeout_seconds,
        )

        rendered_texts = self._rendered_texts(scene)
        manifest = {
            "schema_version": "1.0",
            "renderer": _RENDERER_VERSION,
            "scene_id": scene.scene_id,
            "scene_index": scene.scene_index,
            "rendered_texts": rendered_texts,
            "background_color": scene.visual.background_color,
            "accent_color": scene.visual.accent_color,
            "whiteboard_style": scene.visual.whiteboard_style,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "frame_count": frame_count,
            "narration_sha256": scene.narration_sha256,
            "narrator": self.narrator.name,
            "audio_duration_seconds": audio_duration,
            "video_duration_seconds": video_duration,
            "target_duration_seconds": scene.target_duration_seconds,
            "progressive_reveal": True,
            "presenter_visible": False,
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

        warnings: list[str] = []
        if abs(audio_duration - scene.target_duration_seconds) > 1.5:
            warnings.append(
                f"Narration audio is {audio_duration:.2f}s; target was "
                f"{scene.target_duration_seconds:.2f}s. Measured duration is "
                "recorded, not the target."
            )

        return GeneratedClip(
            video_path=output_path,
            manifest_path=manifest_path,
            provider=self.capabilities.provider_id,
            model=_RENDERER_VERSION,
            request_id=f"local-{uuid.uuid4().hex}",
            text_render_mode=TextRenderMode.DETERMINISTIC,
            renderer=_RENDERER_VERSION,
            narration_sha256=scene.narration_sha256,
            provider_metadata={
                "renderer": _RENDERER_VERSION,
                "narrator": self.narrator.name,
                "whiteboard_style": scene.visual.whiteboard_style,
                "background_color": scene.visual.background_color,
                "accent_color": scene.visual.accent_color,
                "audio_duration_seconds": audio_duration,
                "frame_count": frame_count,
                "progressive_reveal": True,
                "presenter_visible": False,
            },
            warnings=warnings,
        )

    # -------------------------------------------------------------- rendering

    @staticmethod
    def _rendered_texts(scene: SceneSpec) -> list[str]:
        texts: list[str] = []
        if scene.visual.show_scene_title:
            texts.append(scene.title)
        texts.extend(element.text for element in scene.visual.text_elements)
        return texts

    def _frames(
        self, scene: SceneSpec, frame_count: int, duration: float
    ) -> Iterator[bytes]:
        for frame_index in range(frame_count):
            progress = frame_index / max(1, frame_count - 1)
            image = self._render_frame(scene, progress)
            yield image.tobytes()

    def _render_frame(self, scene: SceneSpec, progress: float) -> Image.Image:
        width, height = self.width, self.height
        scale = width / 1280
        background = parse_color(scene.visual.background_color)
        accent = parse_color(scene.visual.accent_color, "#2563EB")
        text_color = contrasting_text_color(scene.visual.background_color)
        grid_color = tuple(
            int(channel * 0.92 + 255 * 0.08) for channel in background
        )
        image = Image.new("RGB", (width, height), background)
        draw = ImageDraw.Draw(image)
        draw_grid(draw, (width, height), color=grid_color, spacing=max(24, int(48 * scale)))

        title_size = max(20, int(44 * scale))
        body_size = max(14, int(30 * scale))
        code_size = max(13, int(25 * scale))
        title_font = load_font(title_size, "bold")
        body_font = load_font(body_size, "regular")
        code_font = load_font(code_size, "mono")

        margin = int(72 * scale)
        y = int(48 * scale)
        if scene.visual.show_scene_title:
            draw.rectangle(
                [(margin, y + 6), (margin + int(8 * scale), y + int(62 * scale))],
                fill=accent,
            )
            title_bottom = draw_wrapped(
                draw,
                scene.title,
                (margin + int(26 * scale), y),
                title_font,
                width - 2 * margin - int(26 * scale),
                text_color,
                line_spacing=max(4, int(8 * scale)),
                max_lines=2,
            )
            y = max(y + int(76 * scale), title_bottom + int(8 * scale))
            draw.line(
                [(margin, y), (width - margin, y)],
                fill=accent,
                width=max(2, int(3 * scale)),
            )
            y += int(34 * scale)
        else:
            y = int(70 * scale)

        body_width = width - 2 * margin
        elements = scene.visual.text_elements
        reveal_count = (
            0
            if not elements
            else min(len(elements), 1 + int(progress * len(elements)))
        )
        active_index = reveal_count - 1 if reveal_count else -1

        if not elements:
            self._draw_abstract_diagram(draw, scene, (margin, y, width - margin, height - int(120 * scale)), accent)
        for index, element in enumerate(elements[:reveal_count]):
            is_active = index == active_index
            if element.kind in {"code", "equation"}:
                box_top = y
                lines = wrap_text(draw, element.text, code_font, body_width - int(36 * scale))
                line_height = code_size + max(4, int(8 * scale))
                box_height = max(int(52 * scale), line_height * len(lines) + int(24 * scale))
                draw.rounded_rectangle(
                    [
                        (margin, box_top),
                        (width - margin, box_top + box_height),
                    ],
                    radius=max(6, int(10 * scale)),
                    fill=(15, 23, 42) if text_color[0] < 128 else (241, 245, 249),
                    outline=accent if is_active else grid_color,
                    width=max(1, int(2 * scale)),
                )
                code_color = (226, 232, 240) if text_color[0] < 128 else (15, 23, 42)
                code_y = box_top + int(12 * scale)
                for line in lines:
                    draw.text(
                        (margin + int(18 * scale), code_y),
                        line,
                        font=code_font,
                        fill=code_color,
                    )
                    code_y += line_height
                y = box_top + box_height + int(18 * scale)
            else:
                bullet_radius = max(4, int(7 * scale))
                if is_active:
                    draw.rounded_rectangle(
                        [
                            (margin - int(12 * scale), y - int(8 * scale)),
                            (width - margin + int(8 * scale), y + int(52 * scale)),
                        ],
                        radius=max(4, int(8 * scale)),
                        fill=tuple(int(channel * 0.90 + accent[channel] * 0.10) for channel in range(3)),
                    )
                draw.ellipse(
                    [
                        (margin, y + int(10 * scale)),
                        (margin + bullet_radius * 2, y + int(10 * scale) + bullet_radius * 2),
                    ],
                    fill=accent,
                )
                text_bottom = draw_wrapped(
                    draw,
                    element.text,
                    (margin + int(30 * scale), y),
                    body_font,
                    body_width - int(30 * scale),
                    text_color,
                    line_spacing=max(4, int(7 * scale)),
                    max_lines=3,
                )
                if is_active or element.emphasis:
                    draw.line(
                        [
                            (margin + int(30 * scale), text_bottom + int(2 * scale)),
                            (width - margin - int(30 * scale), text_bottom + int(2 * scale)),
                        ],
                        fill=accent,
                        width=max(1, int(2 * scale)),
                    )
                y = text_bottom + int(22 * scale)

        # A moving cursor and unlabeled progress bar provide measurable,
        # non-semantic visual activity without adding untracked text.
        cursor_x = margin + int(progress * max(1, body_width - int(30 * scale)))
        cursor_y = min(height - int(82 * scale), y + int(8 * scale))
        draw.ellipse(
            [
                (cursor_x, cursor_y),
                (cursor_x + int(12 * scale), cursor_y + int(12 * scale)),
            ],
            fill=accent,
        )
        bar_y = height - int(38 * scale)
        draw.rounded_rectangle(
            [
                (margin, bar_y),
                (width - margin, bar_y + max(4, int(6 * scale))),
            ],
            radius=max(2, int(3 * scale)),
            fill=grid_color,
        )
        draw.rounded_rectangle(
            [
                (margin, bar_y),
                (margin + int((width - 2 * margin) * progress), bar_y + max(4, int(6 * scale))),
            ],
            radius=max(2, int(3 * scale)),
            fill=accent,
        )
        return image

    def _draw_abstract_diagram(
        self,
        draw: ImageDraw.ImageDraw,
        scene: SceneSpec,
        bounds: tuple[int, int, int, int],
        accent: tuple[int, int, int],
    ) -> None:
        left, top, right, bottom = bounds
        steps = max(1, min(5, len(scene.visual.drawing_steps) or 3))
        width = (right - left) // steps
        for index in range(steps):
            x0 = left + index * width + 8
            x1 = left + (index + 1) * width - 8
            draw.rounded_rectangle(
                [(x0, top + 24), (x1, min(bottom, top + 160))],
                radius=10,
                outline=accent,
                width=2,
            )
            if index < steps - 1:
                draw.line(
                    [(x1 + 2, top + 90), (x1 + width - 16, top + 90)],
                    fill=accent,
                    width=2,
                )

    # -------------------------------------------------------------- encoding

    def _encode_frames(
        self,
        frames: Iterator[bytes],
        output_path: Path,
        *,
        deadline: float,
        request_timeout: float,
    ) -> None:
        command = [
            self.media_tools.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{self.width}x{self.height}",
            "-r",
            str(self.fps),
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            self.preset,
            "-crf",
            str(self.crf),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-y",
            str(output_path),
        ]
        stderr_lines: list[str] = []
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except FileNotFoundError as exc:
            raise ProviderCapabilityError(
                "FFmpeg executable was not found for whiteboard rendering",
                provider=self.capabilities.provider_id,
                cause=exc,
            ) from exc

        def drain() -> None:
            if process.stderr is None:
                return
            for raw_line in process.stderr:
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                stderr_lines.append(line)
                if len(stderr_lines) > 200:
                    del stderr_lines[:50]

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            assert process.stdin is not None
            for frame in frames:
                if time.monotonic() > deadline:
                    raise ProviderTimeoutError(
                        f"Whiteboard rendering exceeded the {request_timeout:.1f}s timeout",
                        provider=self.capabilities.provider_id,
                    )
                process.stdin.write(frame)
            process.stdin.close()
            remaining = max(1.0, deadline - time.monotonic())
            return_code = process.wait(timeout=remaining)
        except (BrokenPipeError, OSError) as exc:
            raise ProviderOutputError(
                "FFmpeg frame pipe failed during whiteboard rendering",
                provider=self.capabilities.provider_id,
                retryable=True,
                details={"stderr_tail": stderr_lines[-8:]},
                cause=exc,
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ProviderTimeoutError(
                f"Whiteboard encoding exceeded the {request_timeout:.1f}s timeout",
                provider=self.capabilities.provider_id,
            ) from exc
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            reader.join(timeout=2)
        if return_code != 0:
            raise ProviderOutputError(
                "FFmpeg failed while encoding whiteboard frames",
                provider=self.capabilities.provider_id,
                retryable=True,
                details={"stderr_tail": stderr_lines[-8:]},
            )

    def _mux_audio(
        self,
        silent_video: Path,
        narration: Path,
        output_path: Path,
        duration: float,
        deadline: float,
        request_timeout: float,
    ) -> None:
        timeout = max(1.0, deadline - time.monotonic())
        command = [
            self.media_tools.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(silent_video),
            "-i",
            str(narration),
            "-filter_complex",
            "[1:a]apad=pad_dur=0.25,aresample=48000[a]",
            "-map",
            "0:v:0",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-t",
            f"{duration:.3f}",
            "-movflags",
            "+faststart",
            "-y",
            str(output_path),
        ]
        try:
            result = self.media_tools.run(
                command,
                timeout_seconds=timeout,
                check=False,
            )
        except MediaToolUnavailableError as exc:
            raise ProviderCapabilityError(
                "FFmpeg executable was not found while muxing narration audio",
                provider=self.capabilities.provider_id,
                cause=exc,
            ) from exc
        except MediaProbeError as exc:
            raise ProviderOutputError(
                "Audio muxing failed or timed out",
                provider=self.capabilities.provider_id,
                retryable=True,
                details={"request_timeout_seconds": request_timeout},
                cause=exc,
            ) from exc
        if result.returncode != 0:
            raise ProviderOutputError(
                "FFmpeg failed while muxing narration audio",
                provider=self.capabilities.provider_id,
                retryable=True,
                details={
                    "exit_code": result.returncode,
                    "stderr_tail": (result.stderr or "").strip().splitlines()[-8:],
                },
            )
        if not output_path.is_file() or output_path.stat().st_size <= 0:
            raise ProviderOutputError(
                "Whiteboard renderer did not produce an output video",
                provider=self.capabilities.provider_id,
                retryable=True,
            )
