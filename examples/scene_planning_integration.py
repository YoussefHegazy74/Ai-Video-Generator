#!/usr/bin/env python3
"""Runnable scene-planning integration example.

Responsibility: demonstrate the complete public handoff for one scene without a
real provider, renderer, TTS system, or packager.
Workflow: build a ``ScenePlanningInput``, run the Script Agent, run the
Whiteboard Director Agent, assemble and round-trip ``ScenePlanningResult``, then
return the selected pages separately for downstream packaging.
Inputs: a deterministic in-memory LLM client and the example planning input.
Outputs: JSON on stdout containing ``scene_planning_result`` and
``selected_pages_for_packaging``.
Interacts with: ``ScriptAgent``, ``WhiteboardDirectorAgent``, the shared
contracts, and the Scene Packager handoff boundary.

Run from the repository root:

    python3 examples/scene_planning_integration.py

The example is deterministic and needs no credentials or network access. It
does not validate a live provider, TTS timing, rendering, packaging, or video
generation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_video_generator.agents.scene_planning.llm import (
    LLMClient,
    LLMRequest,
    LLMResponse,
)
from ai_video_generator.agents.scripting import ScriptAgent
from ai_video_generator.agents.whiteboard import WhiteboardDirectorAgent
from ai_video_generator.models.lesson_plan import (
    AudienceLevel,
    PersonalizationBrief,
    SceneBrief,
)
from ai_video_generator.models.scene import ScenePlanningInput
from ai_video_generator.models.scene_planning import ScenePlanningResult
from ai_video_generator.models.source import (
    PageRange,
    SourceExcerpt,
    SourceReference,
)


SCRIPT_RESPONSE: dict[str, Any] = {
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

WHITEBOARD_RESPONSE: dict[str, Any] = {
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


class DeterministicLLM:
    """Tiny stand-in for a provider adapter implementing ``LLMClient``.

    Replace this with the shared provider adapter when it exists. Both agents
    accept any object with ``generate(request) -> LLMResponse``.
    """

    def __init__(self, *payloads: dict[str, Any]) -> None:
        self._payloads = list(payloads)
        self.requests: list[LLMRequest] = []

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self._payloads:
            raise AssertionError("DeterministicLLM received more calls than expected")
        return LLMResponse(content=json.dumps(self._payloads.pop(0)))


def build_example_input() -> ScenePlanningInput:
    """Build the provisional upstream input for one scene."""
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


def run_scene_planning(
    planning_input: ScenePlanningInput,
    llm_client: LLMClient,
) -> tuple[ScenePlanningResult, tuple[PageRange, ...]]:
    """Run both agents and return the result plus the separate page selection.

    ``ScenePlanningResult`` intentionally does not duplicate
    ``planning_input.selected_pages``. Packaging must receive that tuple
    alongside the result; returning a pair keeps the handoff explicit.
    """
    script = ScriptAgent(llm_client).generate(planning_input)
    whiteboard = WhiteboardDirectorAgent(llm_client).generate_for_scene(
        planning_input,
        script,
        previous_plan=None,  # Only the first scene passes None.
    )
    result = ScenePlanningResult(
        lesson_id=planning_input.lesson_id,
        scene=planning_input.scene,
        script=script,
        whiteboard=whiteboard,
    )
    return result, planning_input.selected_pages


def build_packaging_payload(
    result: ScenePlanningResult,
    selected_pages: tuple[PageRange, ...],
) -> dict[str, Any]:
    """Validate the result round-trip and build the JSON packaging handoff."""
    serialized_result = result.to_dict()
    if ScenePlanningResult.from_dict(serialized_result) != result:
        raise RuntimeError("ScenePlanningResult did not round-trip exactly")

    payload = {
        "scene_planning_result": serialized_result,
        "selected_pages_for_packaging": [
            page_range.to_dict() for page_range in selected_pages
        ],
    }
    # Fail here if any value is not JSON-serializable.
    json.dumps(payload, ensure_ascii=False)
    return payload


def build_example_payload() -> dict[str, Any]:
    """Run the full example and return the packaging handoff."""
    llm = DeterministicLLM(SCRIPT_RESPONSE, WHITEBOARD_RESPONSE)
    result, selected_pages = run_scene_planning(build_example_input(), llm)
    return build_packaging_payload(result, selected_pages)


def main() -> int:
    print(json.dumps(build_example_payload(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
