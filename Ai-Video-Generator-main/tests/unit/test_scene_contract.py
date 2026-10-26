from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_video_generator.core.constants import MediaKind, SceneStatus
from ai_video_generator.core.exceptions import SceneContractError, SceneStateError
from ai_video_generator.video.scene_contract import (
    GenerationInfo,
    MediaAsset,
    SceneMedia,
    SceneSet,
    SceneSpec,
    VisualSpec,
)

from tests.helpers import make_generated_scene, make_scene, make_scene_set


def _asset(key: str = "jobs/job_test/scenes/000-scene-1/video.mp4") -> MediaAsset:
    return MediaAsset(
        artifact_key=key,
        kind=MediaKind.VIDEO,
        size_bytes=1234,
        sha256="a" * 64,
        has_audio=True,
    )


def _generation() -> GenerationInfo:
    return GenerationInfo(provider="test", requested_duration_seconds=1.0)


def test_valid_scene_contract_round_trips() -> None:
    scene = make_scene()
    payload = scene.model_dump(mode="json")
    restored = SceneSpec.model_validate(payload)
    assert restored.scene_id == scene.scene_id
    assert restored.narration_sha256 == scene.narration_sha256


def test_expected_visible_text_includes_title_first() -> None:
    scene = make_scene(texts=("Alpha", "Beta"))
    assert scene.expected_visible_texts == [scene.title, "Alpha", "Beta"]


def test_duplicate_scene_ids_rejected() -> None:
    with pytest.raises(ValidationError):
        SceneSet(
            scenes=[
                make_scene("same", scene_index=0),
                make_scene("same", scene_index=1),
            ]
        )


def test_duplicate_scene_indices_rejected() -> None:
    with pytest.raises(ValidationError):
        SceneSet(
            scenes=[
                make_scene("one", scene_index=0),
                make_scene("two", scene_index=0),
            ]
        )


def test_non_contiguous_indices_rejected() -> None:
    with pytest.raises(ValidationError):
        SceneSet(
            scenes=[
                make_scene("one", scene_index=0),
                make_scene("two", scene_index=2),
            ]
        )


def test_illegal_transition_rejected() -> None:
    scene = make_scene()
    with pytest.raises(SceneStateError):
        scene.transition_to(SceneStatus.VALIDATED)


def test_validated_scene_requires_report() -> None:
    payload = make_scene().model_dump(mode="json")
    payload.update(
        status=SceneStatus.VALIDATED.value,
        media=SceneMedia(video=_asset(), audio_embedded=True).model_dump(
            mode="json"
        ),
        generation=_generation().model_dump(mode="json"),
        validation=None,
    )
    with pytest.raises(ValidationError):
        SceneSpec.model_validate(payload)


def test_failed_scene_requires_failure_detail() -> None:
    payload = make_scene().model_dump(mode="json")
    payload.update(
        status=SceneStatus.FAILED.value,
        media=SceneMedia(video=_asset(), audio_embedded=True).model_dump(
            mode="json"
        ),
        generation=_generation().model_dump(mode="json"),
        failure=None,
    )
    with pytest.raises(ValidationError):
        SceneSpec.model_validate(payload)


def test_media_asset_rejects_path_traversal() -> None:
    with pytest.raises(ValidationError):
        _asset("../outside.mp4")
    with pytest.raises(ValidationError):
        _asset("C:/outside.mp4")


def test_generation_metadata_redacts_secret_keys() -> None:
    info = GenerationInfo(
        provider="test",
        requested_duration_seconds=1.0,
        provider_metadata={
            "api_key": "should-not-leak",
            "nested": {"authorization": "should-not-leak"},
            "safe": "visible",
        },
    )
    assert info.provider_metadata["api_key"] == "<redacted>"
    assert info.provider_metadata["nested"]["authorization"] == "<redacted>"
    assert info.provider_metadata["safe"] == "visible"


def test_visual_rejects_blank_instructions() -> None:
    with pytest.raises(ValidationError):
        VisualSpec(instructions=[""])


def test_scene_set_requires_assembly_ready() -> None:
    scene = make_scene()
    scene_set = make_scene_set([scene])
    with pytest.raises(SceneContractError):
        scene_set.require_assembly_ready()


def test_replace_unknown_scene_rejected() -> None:
    scene_set = make_scene_set([make_scene("one")])
    with pytest.raises(SceneContractError):
        scene_set.replace_scene(make_scene("two"))


def test_scene_set_orders_scenes_by_index(
    artifact_storage, media_tools, tmp_path
) -> None:
    clip = tmp_path / "clip.mp4"
    from tests.helpers import create_test_clip

    create_test_clip(clip, media_tools, duration=1.0)
    first = make_generated_scene(
        make_scene("first", scene_index=0),
        artifact_storage,
        media_tools,
        clip,
    )
    second = make_generated_scene(
        make_scene("second", scene_index=1),
        artifact_storage,
        media_tools,
        clip,
    )
    scene_set = make_scene_set([second, first])
    assert [scene.scene_id for scene in scene_set.ordered_scenes] == [
        "first",
        "second",
    ]
