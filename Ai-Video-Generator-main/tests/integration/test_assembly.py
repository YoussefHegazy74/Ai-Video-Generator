from __future__ import annotations

from pathlib import Path

import pytest

from ai_video_generator.core.constants import TransitionStyle
from ai_video_generator.core.exceptions import AssemblyInputError
from ai_video_generator.services.storage_service import ArtifactStorageService
from ai_video_generator.services.video_assembly_service import VideoAssemblyService
from ai_video_generator.video.assembly import AssemblyOptions, FFmpegAssembler
from ai_video_generator.video.media_probe import FFmpegTools

from tests.helpers import (
    create_test_clip,
    make_generated_scene,
    make_scene,
    make_scene_set,
    make_validated_scene,
)


def _options(**overrides) -> AssemblyOptions:
    values = {
        "include_intro": False,
        "include_outro": False,
        "transition": TransitionStyle.CUT,
        "width": 320,
        "height": 180,
        "fps": 10,
        "show_scene_titles": False,
        "show_progress": False,
        "preset": "ultrafast",
        "crf": 28,
        "duration_tolerance_seconds": 1.5,
    }
    values.update(overrides)
    return AssemblyOptions(**values)


def _validated_scene(
    artifact_storage: ArtifactStorageService,
    media_tools: FFmpegTools,
    tmp_path: Path,
    *,
    scene_id: str,
    scene_index: int,
    duration: float,
):
    clip = create_test_clip(
        tmp_path / f"{scene_id}.mp4",
        media_tools,
        duration=duration,
        width=320,
        height=180,
        fps=10,
    )
    scene = make_scene(
        scene_id,
        scene_index=scene_index,
        target_duration=duration,
    )
    generated = make_generated_scene(
        scene,
        artifact_storage,
        media_tools,
        clip,
        job_id="job_assembly",
        with_manifest=True,
    )
    return make_validated_scene(generated)


def _assembly_service(
    media_tools: FFmpegTools,
    artifact_storage: ArtifactStorageService,
    options: AssemblyOptions,
) -> VideoAssemblyService:
    return VideoAssemblyService(
        FFmpegAssembler(media_tools, options),
        artifact_storage,
        media_tools,
        audio_silence_mean_db=-50.0,
        audio_silence_max_db=-45.0,
    )


def test_build_plan_uses_scene_index_order_and_explicit_durations(
    artifact_storage: ArtifactStorageService,
    media_tools: FFmpegTools,
    tmp_path: Path,
) -> None:
    first = _validated_scene(
        artifact_storage,
        media_tools,
        tmp_path,
        scene_id="first",
        scene_index=0,
        duration=1.0,
    )
    second = _validated_scene(
        artifact_storage,
        media_tools,
        tmp_path,
        scene_id="second",
        scene_index=1,
        duration=1.2,
    )
    scene_set = make_scene_set([second, first])
    assembler = FFmpegAssembler(media_tools, _options())
    source_paths = {
        scene.scene_id: artifact_storage.absolute_path(scene.media.video.artifact_key)
        for scene in scene_set.scenes
        if scene.media.video is not None
    }

    plan = assembler.build_plan(scene_set, "job_plan", source_paths)

    assert plan.scene_order == ["first", "second"]
    assert [entry.scene_index for entry in plan.scene_entries] == [0, 1]
    assert plan.expected_duration_seconds == pytest.approx(2.2, abs=0.1)


def test_assembly_service_normalizes_and_verifies_final_video(
    artifact_storage: ArtifactStorageService,
    media_tools: FFmpegTools,
    tmp_path: Path,
) -> None:
    first = _validated_scene(
        artifact_storage,
        media_tools,
        tmp_path,
        scene_id="first",
        scene_index=0,
        duration=1.0,
    )
    second = _validated_scene(
        artifact_storage,
        media_tools,
        tmp_path,
        scene_id="second",
        scene_index=1,
        duration=1.2,
    )
    scene_set = make_scene_set([first, second])
    service = _assembly_service(
        media_tools,
        artifact_storage,
        _options(
            include_intro=True,
            include_outro=True,
            intro_duration_seconds=0.6,
            outro_duration_seconds=0.6,
        ),
    )

    result = service.assemble(scene_set, job_id="job_assembly")

    final_path = artifact_storage.absolute_path(result.final_video_key)
    assert final_path.is_file()
    assert final_path.stat().st_size > 0
    assert result.manifest.scene_order == ["first", "second"]
    assert result.manifest.intro_included is True
    assert result.manifest.outro_included is True
    assert result.manifest.container == "mp4"
    assert result.manifest.width == 320
    assert result.manifest.height == 180
    assert result.manifest.fps == pytest.approx(10.0, abs=0.1)
    assert result.manifest.video_codec == "h264"
    assert result.manifest.audio_codec == "aac"
    assert result.manifest.duration_seconds == pytest.approx(
        result.manifest.expected_duration_seconds, abs=1.5
    )
    assert result.verification.audio_not_silent is True
    assert artifact_storage.exists(result.manifest_key)
    assert artifact_storage.exists(result.final_video_key)

    info = media_tools.probe(final_path)
    assert info.video is not None
    assert info.audio is not None
    assert info.video.width == 320
    assert info.video.height == 180


def test_assembly_service_refuses_unvalidated_scenes(
    artifact_storage: ArtifactStorageService,
    media_tools: FFmpegTools,
    tmp_path: Path,
) -> None:
    clip = create_test_clip(
        tmp_path / "not-validated.mp4",
        media_tools,
        duration=1.0,
        width=320,
        height=180,
        fps=10,
    )
    scene = make_scene("not-validated", target_duration=1.0)
    generated = make_generated_scene(
        scene,
        artifact_storage,
        media_tools,
        clip,
        job_id="job_assembly",
        with_manifest=True,
    )
    service = _assembly_service(media_tools, artifact_storage, _options())

    with pytest.raises(AssemblyInputError) as error:
        service.assemble(make_scene_set([generated]), job_id="job_assembly")

    assert "not assembly-ready" in error.value.message
