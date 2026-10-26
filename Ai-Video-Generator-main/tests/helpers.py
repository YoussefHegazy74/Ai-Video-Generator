"""Shared test helpers. No network and no paid API calls."""

from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path
from typing import Iterable

from ai_video_generator.core.constants import (
    ArtifactKind,
    CheckStatus,
    MediaKind,
    SceneStatus,
    Severity,
    TextRenderMode,
)
from ai_video_generator.core.exceptions import MediaToolUnavailableError
from ai_video_generator.integrations.audiovisual_generation_provider import (
    DurationSelection,
    GeneratedClip,
    GenerationRequest,
    ProviderCapabilities,
)
from ai_video_generator.services.storage_service import ArtifactStorageService
from ai_video_generator.video.media_probe import FFmpegTools
from ai_video_generator.video.scene_contract import (
    GenerationInfo,
    MediaAsset,
    SceneMedia,
    SceneSet,
    SceneSpec,
    ValidationCheck,
    ValidationReport,
    VisualSpec,
)


class FakeNarrator:
    """Deterministic local WAV writer used instead of Windows SAPI/flite."""

    name = "fake_narrator"

    def __init__(self, duration_seconds: float = 1.2, *, silent: bool = False) -> None:
        self.duration_seconds = duration_seconds
        self.silent = silent
        self.calls: list[tuple[str, Path]] = []

    def available(self) -> bool:
        return True

    def synthesize(
        self, text: str, output_path: Path, *, timeout_seconds: float = 60.0
    ) -> None:
        self.calls.append((text, Path(output_path)))
        _write_wav(
            Path(output_path),
            duration_seconds=self.duration_seconds,
            silent=self.silent,
        )


class FakeProvider:
    """Deterministic provider used to exercise generation without a network."""

    capabilities = ProviderCapabilities(
        provider_id="fake_provider",
        display_name="Fake deterministic provider",
        generates_synchronized_audio=True,
        supports_duration_control=True,
        min_duration_seconds=0.1,
        max_duration_seconds=120.0,
        supports_separate_audio=False,
        supports_idempotency_key=True,
        output_container="mp4",
        text_render_mode=TextRenderMode.DETERMINISTIC,
        requires_credentials=False,
        is_local=True,
    )

    def __init__(
        self,
        media_tools: FFmpegTools,
        *,
        clip_duration: float = 1.0,
        failure_plan: dict[int, BaseException] | None = None,
        silent_attempts: Iterable[int] = (),
        width: int = 320,
        height: int = 180,
        fps: int = 10,
    ) -> None:
        self.media_tools = media_tools
        self.clip_duration = clip_duration
        self.failure_plan = dict(failure_plan or {})
        self.silent_attempts = set(silent_attempts)
        self.width = width
        self.height = height
        self.fps = fps
        self.requests: list[GenerationRequest] = []

    def check_ready(self) -> None:
        return None

    def resolve_duration(self, requested_seconds: float) -> DurationSelection:
        return DurationSelection(
            requested_seconds=requested_seconds,
            selected_seconds=requested_seconds,
            duration_control=True,
            note="Fake provider honors the requested duration for test purposes.",
        )

    def generate(self, request: GenerationRequest) -> GeneratedClip:
        self.requests.append(request)
        call = len(self.requests)
        failure = self.failure_plan.get(call)
        if failure is not None:
            raise failure
        scene = request.scene
        video_path = Path(request.output_dir) / f"attempt-{call}.mp4"
        create_test_clip(
            video_path,
            self.media_tools,
            duration=self.clip_duration,
            width=self.width,
            height=self.height,
            fps=self.fps,
            audio=True,
            silent_audio=call in self.silent_attempts,
        )
        manifest_path = Path(request.output_dir) / f"attempt-{call}.manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "rendered_texts": scene.expected_visible_texts,
                    "background_color": scene.visual.background_color,
                    "whiteboard_style": scene.visual.whiteboard_style,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return GeneratedClip(
            video_path=video_path,
            manifest_path=manifest_path,
            provider=self.capabilities.provider_id,
            model="fake-model",
            request_id=f"fake-{call}",
            text_render_mode=TextRenderMode.DETERMINISTIC,
            renderer="fake-renderer",
            narration_sha256=scene.narration_sha256,
            provider_metadata={
                "whiteboard_style": scene.visual.whiteboard_style,
                "call": call,
            },
        )


def _write_wav(path: Path, *, duration_seconds: float, silent: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 22050
    frame_count = max(1, int(duration_seconds * sample_rate))
    amplitude = 0 if silent else 9000
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        frames = bytearray()
        for index in range(frame_count):
            value = (
                0
                if silent
                else int(
                    amplitude
                    * math.sin(2 * math.pi * 440 * index / sample_rate)
                )
            )
            frames.extend(struct.pack("<h", value))
        handle.writeframes(bytes(frames))


def create_test_clip(
    path: Path,
    media_tools: FFmpegTools,
    *,
    duration: float = 1.5,
    width: int = 320,
    height: int = 180,
    fps: int = 10,
    audio: bool = True,
    audio_duration: float | None = None,
    silent_audio: bool = False,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        media_tools.ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={width}x{height}:rate={fps}:duration={duration:.3f}",
    ]
    if audio:
        source = "anullsrc=channel_layout=mono:sample_rate=44100"
        if not silent_audio:
            source = "sine=frequency=440:sample_rate=44100"
        command.extend(["-f", "lavfi", "-i", source])
        if audio_duration is not None:
            command.extend(["-t", f"{audio_duration:.3f}"])
    command.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
        ]
    )
    if audio:
        command.extend(["-c:a", "aac", "-b:a", "96k"])
    command.extend(
        ["-t", f"{duration:.3f}", "-movflags", "+faststart", "-y", str(path)]
    )
    result = subprocess.run(
        command,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Test clip generation failed: "
            + " | ".join((result.stderr or "").strip().splitlines()[-8:])
        )
    return path


def make_visual(
    *,
    style: str = "clean_whiteboard",
    texts: Iterable[str] = ("A test bullet",),
) -> VisualSpec:
    return VisualSpec(
        whiteboard_style=style,
        instructions=["Draw the test content."],
        text_elements=[{"text": text, "kind": "body"} for text in texts],
        drawing_steps=["Draw a box."],
    )


def make_scene(
    scene_id: str = "scene-1",
    *,
    scene_index: int = 0,
    target_duration: float = 1.5,
    narration: str = "This is a short deterministic test narration.",
    style: str = "clean_whiteboard",
    texts: Iterable[str] = ("A test bullet",),
) -> SceneSpec:
    return SceneSpec(
        scene_id=scene_id,
        scene_index=scene_index,
        title=f"Title {scene_id}",
        learning_objective="Verify the test pipeline.",
        narration=narration,
        visual=make_visual(style=style, texts=texts),
        target_duration_seconds=target_duration,
    )


def make_generation_info(
    scene: SceneSpec,
    *,
    provider: str = "test_provider",
    text_render_mode: TextRenderMode = TextRenderMode.DETERMINISTIC,
) -> GenerationInfo:
    return GenerationInfo(
        provider=provider,
        model="test-model",
        requested_duration_seconds=scene.target_duration_seconds,
        selected_duration_seconds=scene.target_duration_seconds,
        duration_control=True,
        text_render_mode=text_render_mode,
        renderer="test-renderer",
        narration_sha256=scene.narration_sha256,
        provider_metadata={"whiteboard_style": scene.visual.whiteboard_style},
    )


def make_generated_scene(
    scene: SceneSpec,
    storage: ArtifactStorageService,
    media_tools: FFmpegTools,
    clip_path: Path,
    *,
    job_id: str = "job_test",
    text_render_mode: TextRenderMode = TextRenderMode.DETERMINISTIC,
    with_manifest: bool = False,
    manifest_texts: list[str] | None = None,
) -> SceneSpec:
    prefix = storage.scene_prefix(job_id, scene.scene_index, scene.scene_id)
    record = storage.store_file(
        clip_path,
        f"{prefix}/video.mp4",
        ArtifactKind.SCENE_VIDEO,
        job_id=job_id,
        scene_id=scene.scene_id,
    )
    info = media_tools.probe(clip_path)
    video_asset = MediaAsset(
        artifact_key=record.key,
        kind=MediaKind.VIDEO,
        mime_type="video/mp4",
        size_bytes=record.size_bytes,
        sha256=record.sha256,
        duration_seconds=info.duration_seconds,
        width=info.video.width if info.video else None,
        height=info.video.height if info.video else None,
        fps=info.video.fps if info.video else None,
        video_codec=info.video.codec_name if info.video else None,
        audio_codec=info.audio.codec_name if info.audio else None,
        container=info.format_name,
        has_audio=info.audio is not None,
        sample_rate=info.audio.sample_rate if info.audio else None,
        channels=info.audio.channels if info.audio else None,
        video_streams=info.video_streams,
        audio_streams=info.audio_streams,
    )
    manifest_asset = None
    if with_manifest:
        manifest_record = storage.store_json(
            {
                "rendered_texts": (
                    manifest_texts
                    if manifest_texts is not None
                    else scene.expected_visible_texts
                ),
                "background_color": scene.visual.background_color,
                "whiteboard_style": scene.visual.whiteboard_style,
            },
            f"{prefix}/manifest.json",
            ArtifactKind.SCENE_MANIFEST,
            job_id=job_id,
            scene_id=scene.scene_id,
        )
        manifest_asset = MediaAsset(
            artifact_key=manifest_record.key,
            kind=MediaKind.MANIFEST,
            mime_type="application/json",
            size_bytes=manifest_record.size_bytes,
            sha256=manifest_record.sha256,
        )
    scene.media = SceneMedia(
        video=video_asset,
        manifest=manifest_asset,
        audio_embedded=video_asset.has_audio is True,
    )
    scene.generation = make_generation_info(
        scene, text_render_mode=text_render_mode
    )
    scene.status = SceneStatus.GENERATED
    return scene


def passing_report(scene_id: str) -> ValidationReport:
    return ValidationReport(
        scene_id=scene_id,
        checks=[
            ValidationCheck(
                check_id="test.pass",
                category="test",
                status=CheckStatus.PASS,
                severity=Severity.INFO,
                message="Test check passed.",
            )
        ],
    )


def make_validated_scene(scene: SceneSpec) -> SceneSpec:
    scene.status = SceneStatus.VALIDATED
    scene.validation = passing_report(scene.scene_id)
    return scene


def make_scene_set(
    scenes: list[SceneSpec], *, expected_style: str | None = "clean_whiteboard"
) -> SceneSet:
    return SceneSet(
        course_title="Test Course",
        expected_style=expected_style,
        scenes=scenes,
    )


def copy_clip(source: Path, destination: Path) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return destination


def write_json(path: Path, payload: object) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def require_ffmpeg(media_tools: FFmpegTools) -> None:
    try:
        media_tools.ensure_available()
    except MediaToolUnavailableError as exc:  # pragma: no cover - environment-dependent
        import pytest

        pytest.skip(str(exc))
