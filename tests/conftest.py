"""Shared test setup and fixtures for scene-planning component tests.

Responsibility: make the ``src`` layout importable without requiring packaging
setup, and provide small deterministic contracts and fake LLM responses.
Workflow: pytest bootstrap for unit tests only.
Inputs: none.
Outputs: fixtures for planning inputs, script payloads, whiteboard payloads, and
a configurable fake LLM client.
Interacts with: the Script Agent, Whiteboard Director Agent, model contracts, and
validation tests.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_video_generator.agents.scene_planning.llm import LLMRequest, LLMResponse
from ai_video_generator.models.lesson_plan import (
    AudienceLevel,
    PersonalizationBrief,
    SceneBrief,
)
from ai_video_generator.models.scene import ScenePlanningInput
from ai_video_generator.models.source import PageRange, SourceExcerpt, SourceReference
from ai_video_generator.services.storage_service import ArtifactStorageService
from ai_video_generator.storage.artifact_registry import ArtifactRegistry
from ai_video_generator.storage.file_storage import LocalFileStorage
from ai_video_generator.video.audio_video_validation import ValidationPolicy
from ai_video_generator.video.media_probe import FFmpegTools

from tests.helpers import require_ffmpeg


class FakeLLM:
    """Deterministic LLM client used to test structured agent behavior."""

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.requests: list[LLMRequest] = []

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("FakeLLM received more calls than expected")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, LLMResponse):
            return response
        if isinstance(response, str):
            return LLMResponse(content=response)
        return LLMResponse(content=json.dumps(response))


@pytest.fixture
def planning_input() -> ScenePlanningInput:
    excerpts = (
        SourceExcerpt(
            source_id="src-1",
            reference=SourceReference(
                document_id="book-1",
                page_number=12,
                section_title="Neural Networks",
            ),
            text=(
                "A neural network is organized in layers. Each layer contains "
                "connected units that pass information forward."
            ),
        ),
        SourceExcerpt(
            source_id="src-2",
            reference=SourceReference(
                document_id="book-1",
                page_number=23,
                section_title="Training",
            ),
            text=(
                "Training adjusts the network's weights to reduce error. After "
                "training, the network can make predictions for new inputs."
            ),
        ),
    )
    scene = SceneBrief(
        scene_id="scene-001",
        sequence=1,
        title="Neural Networks",
        objective="Explain layers and training to a beginner.",
        topics=("layers", "training", "prediction"),
        target_duration_seconds=30.0,
        source_excerpts=excerpts,
    )
    preferences = PersonalizationBrief(
        audience_level=AudienceLevel.BEGINNER,
        language="en",
        teaching_style="football",
        style_guidance="Use football analogies without changing the source facts.",
    )
    return ScenePlanningInput(
        lesson_id="lesson-001",
        scene=scene,
        preferences=preferences,
        selected_pages=(PageRange(10, 20), PageRange(22, 24)),
    )


@pytest.fixture
def script_payload() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "scene_id": "scene-001",
        "language": "en",
        "segments": [
            {
                "segment_id": "seg-1",
                "text": (
                    "A neural network is like a football team: each layer contains "
                    "players who pass information forward to the next group."
                ),
                "estimated_duration_seconds": 12.0,
                "key_point_ids": ["kp-1"],
            },
            {
                "segment_id": "seg-2",
                "text": (
                    "Training is the practice session; the network adjusts its weights "
                    "after each attempt so its passes become more accurate."
                ),
                "estimated_duration_seconds": 10.0,
                "key_point_ids": ["kp-2"],
            },
            {
                "segment_id": "seg-3",
                "text": (
                    "After enough practice, the model can predict the next play from "
                    "new input."
                ),
                "estimated_duration_seconds": 8.0,
                "key_point_ids": ["kp-2"],
            },
        ],
        "key_points": [
            {
                "key_point_id": "kp-1",
                "text": "A neural network is organized in connected layers.",
                "source_ids": ["src-1"],
                "importance": "core",
            },
            {
                "key_point_id": "kp-2",
                "text": "Training adjusts weights so the model can make predictions.",
                "source_ids": ["src-2"],
                "importance": "core",
            },
        ],
        "delivery_notes": ["Pause briefly before the prediction summary."],
    }


@pytest.fixture
def whiteboard_payload() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "scene_id": "scene-001",
        "layout": {
            "regions": [
                {
                    "region_id": "title",
                    "kind": "title",
                    "x": 0.0,
                    "y": 0.0,
                    "width": 1.0,
                    "height": 0.12,
                },
                {
                    "region_id": "main",
                    "kind": "content",
                    "x": 0.02,
                    "y": 0.16,
                    "width": 0.96,
                    "height": 0.72,
                },
                {
                    "region_id": "summary",
                    "kind": "summary",
                    "x": 0.02,
                    "y": 0.91,
                    "width": 0.96,
                    "height": 0.07,
                },
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
                    "text": "Neural network = connected layers",
                },
                {
                    "action_id": "act-2",
                    "action_type": "write_text",
                    "segment_id": "seg-2",
                    "region_id": "main",
                    "order": 2,
                    "text": "Training adjusts weights",
                },
                {
                    "action_id": "act-3",
                    "action_type": "draw_arrow",
                    "segment_id": "seg-2",
                    "region_id": "main",
                    "order": 3,
                    "points": [
                        {"x": 0.2, "y": 0.4},
                        {"x": 0.5, "y": 0.4},
                    ],
                },
                {
                    "action_id": "act-4",
                    "action_type": "write_text",
                    "segment_id": "seg-3",
                    "region_id": "summary",
                    "order": 4,
                    "text": "Predict the next play",
                },
            ]
        },
        "handwriting": {
            "cues": [
                {
                    "cue_id": "hw-1",
                    "action_id": "act-1",
                    "mode": "handwrite",
                    "order": 1,
                },
                {
                    "cue_id": "hw-2",
                    "action_id": "act-2",
                    "mode": "handwrite",
                    "order": 2,
                },
                {
                    "cue_id": "hw-3",
                    "action_id": "act-4",
                    "mode": "handwrite",
                    "order": 3,
                },
            ]
        },
        "highlights": {
            "cues": [
                {
                    "highlight_id": "hl-1",
                    "target_action_id": "act-2",
                    "segment_id": "seg-3",
                    "style": "marker",
                    "order": 1,
                }
            ]
        },
        "continuity": {
            "mode": "fresh_board",
            "carry_over_action_ids": [],
            "clear_region_ids": [],
            "notes": [],
        },
        "presentation_notes": [
            "Keep the board clean and reveal one idea at a time.",
        ],
    }


@pytest.fixture
def fake_llm_factory():
    return FakeLLM


# ---------------------------------------------------------------------------
# Runtime generation / validation / assembly fixtures
#
# These support the media pipeline tests. FFmpeg-dependent tests skip with an
# explicit prerequisite message when the tools are unavailable.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return ROOT


@pytest.fixture()
def media_tools() -> FFmpegTools:
    tools = FFmpegTools(timeout_seconds=30)
    require_ffmpeg(tools)
    return tools


@pytest.fixture()
def file_storage(tmp_path: Path) -> LocalFileStorage:
    return LocalFileStorage(tmp_path / "artifacts", temp_root=tmp_path / "work")


@pytest.fixture()
def artifact_storage(
    file_storage: LocalFileStorage, tmp_path: Path
) -> ArtifactStorageService:
    registry = ArtifactRegistry(tmp_path / "artifacts" / "artifact_registry.json")
    return ArtifactStorageService(file_storage, registry)


@pytest.fixture()
def validation_policy() -> ValidationPolicy:
    return ValidationPolicy(
        require_audio=True,
        duration_warning_tolerance_seconds=0.5,
        duration_failure_tolerance_seconds=1.0,
        min_scene_duration_seconds=0.5,
        max_scene_duration_seconds=30.0,
        sync_tolerance_seconds=0.35,
        sync_failure_tolerance_seconds=0.9,
        min_width=160,
        min_height=90,
        min_fps=5.0,
        min_media_bytes=1024,
        blank_frame_stddev_threshold=2.0,
    )
