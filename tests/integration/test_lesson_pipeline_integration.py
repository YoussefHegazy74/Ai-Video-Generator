"""Integrated Script -> Whiteboard -> Visual -> Packaging -> Media pipeline test.

Responsibility: prove that the planning stack and the runtime stack exchange the
correct contracts end to end, using a deterministic fake LLM and fake provider.
Workflow: one short scene is planned, packaged, adapted, generated, validated,
and assembled with real FFmpeg.
Inputs: deterministic planning payloads and synthetic media.
Outputs: a terminal job, persisted scene records, and a verified final video.
Interacts with: the orchestration package, services factories, and storage.
"""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from ai_video_generator.agents.orchestration.lesson_pipeline import LessonPipeline
from ai_video_generator.agents.orchestration.runtime_adapter import (
    SceneRuntimeAdapter,
)
from ai_video_generator.core.constants import TransitionStyle
from ai_video_generator.core.settings import load_settings
from ai_video_generator.models.lesson_plan import LessonPlan
from ai_video_generator.services.pipeline_factory import (
    build_planning_pipeline,
    build_runtime_pipeline,
)

from tests.helpers import FakeProvider, require_ffmpeg

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


SCRIPT = {
    "schema_version": "1.0",
    "scene_id": "scene-001",
    "language": "en",
    "segments": [
        {
            "segment_id": "seg-1",
            "text": "A fraction is a part of a whole.",
            "estimated_duration_seconds": 1.0,
            "key_point_ids": ["kp-1"],
        }
    ],
    "key_points": [
        {
            "key_point_id": "kp-1",
            "text": "A fraction is a part of a whole.",
            "source_ids": ["src-1"],
            "importance": "core",
        }
    ],
    "delivery_notes": ["Speak slowly."],
}

WHITEBOARD = {
    "schema_version": "1.0",
    "scene_id": "scene-001",
    "layout": {
        "regions": [
            {
                "region_id": "main",
                "kind": "content",
                "x": 0.0,
                "y": 0.0,
                "width": 1.0,
                "height": 1.0,
            }
        ]
    },
    "drawing": {
        "actions": [
            {
                "action_id": "act-1",
                "action_type": "write_text",
                "segment_id": "seg-1",
                "region_id": "main",
                "order": 1,
                "text": "One half",
            }
        ]
    },
    "handwriting": {
        "cues": [
            {"cue_id": "hw-1", "action_id": "act-1", "mode": "handwrite", "order": 1}
        ]
    },
    "highlights": {"cues": []},
    "continuity": {
        "mode": "fresh_board",
        "carry_over_action_ids": [],
        "clear_region_ids": [],
        "notes": [],
    },
    "presentation_notes": ["Write one half on the board."],
}

VISUAL = {
    "scene_id": "scene-001",
    "items": [
        {
            "visual_id": "vis-1",
            "purpose": "explain_concept",
            "kind": "analogy",
            "description": "Show a pizza cut into two equal slices.",
            "segment_ids": ["seg-1"],
            "key_point_ids": ["kp-1"],
            "source_ids": ["src-1"],
            "exact_text": ["1/2"],
        }
    ],
}


def _lesson_plan(planning_input) -> LessonPlan:
    brief = replace(planning_input.scene, target_duration_seconds=1.0)
    return LessonPlan(
        lesson_id=planning_input.lesson_id,
        title="Fractions",
        selected_pages=planning_input.selected_pages,
        preferences=planning_input.preferences,
        scenes=(brief,),
    )


def test_integrated_lesson_pipeline_generates_a_verified_video(
    planning_input,
    fake_llm_factory,
    validation_policy,
    tmp_path,
    media_tools,
):
    require_ffmpeg(media_tools)
    lesson_plan = _lesson_plan(planning_input)

    settings = load_settings(
        config_dir=REPO_ROOT / "config",
        project_root=REPO_ROOT,
    )
    settings = settings.model_copy(
        update={
            "storage": settings.storage.model_copy(
                update={
                    "root": tmp_path / "artifacts",
                    "temp_root": tmp_path / "work",
                    "registry_path": tmp_path / "artifacts" / "registry.json",
                }
            ),
            "generation": settings.generation.model_copy(
                update={"max_attempts": 1, "max_validation_attempts": 1}
            ),
            "planning": settings.planning.model_copy(
                update={
                    "min_scene_duration_seconds": 0.5,
                    "max_scene_duration_seconds": 30.0,
                }
            ),
            "validation": validation_policy,
            "assembly": settings.assembly.model_copy(
                update={
                    "include_intro": False,
                    "include_outro": False,
                    "transition": TransitionStyle.CUT,
                    "width": 320,
                    "height": 180,
                    "fps": 10,
                    "show_progress": False,
                }
            ),
        }
    )

    llm = fake_llm_factory(
        copy.deepcopy(SCRIPT),
        copy.deepcopy(WHITEBOARD),
        copy.deepcopy(VISUAL),
    )
    planning_components = build_planning_pipeline(settings, llm)

    provider = FakeProvider(
        media_tools, clip_duration=1.0, width=320, height=180, fps=10
    )
    runtime_components = build_runtime_pipeline(
        settings, provider=provider, sleep=lambda _: None
    )

    pipeline = LessonPipeline(
        planning_components.planning,
        SceneRuntimeAdapter(),
        runtime_components.orchestrator,
        options=planning_components.runtime_options,
    )
    result = pipeline.run(lesson_plan, job_id="integrated-lesson")

    assert result.job.status.value == "completed"
    assert result.job.final_video_key is not None
    assert runtime_components.storage.exists(result.job.final_video_key)
    assert result.scene_set.project_id == lesson_plan.lesson_id
    assert [scene.scene_id for scene in result.scene_set.scenes] == ["scene-001"]

    runtime_scene = result.scene_set.scenes[0]
    assert runtime_scene.narration == "A fraction is a part of a whole."
    assert runtime_scene.target_duration_seconds == pytest.approx(1.0)
    assert runtime_scene.source_references[0].page_numbers == [12]
    assert [element.text for element in runtime_scene.visual.text_elements] == [
        "One half",
        "1/2",
    ]

    # The provider received the adapted runtime contract, not planning models.
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.scene.scene_id == "scene-001"
    assert request.scene.narration == runtime_scene.narration
    assert request.requested_duration_seconds == pytest.approx(1.0)

    scene_record = runtime_components.storage.read_json(
        "jobs/integrated-lesson/scenes/000-scene-001/scene.json"
    )
    assert scene_record["status"] == "validated"
    assert scene_record["source_references"][0]["document_id"] == "book-1"

    final_path = runtime_components.storage.absolute_path(result.job.final_video_key)
    info = media_tools.probe(final_path)
    assert info.video is not None
    assert info.audio is not None
    assert info.duration_seconds > 0.5
