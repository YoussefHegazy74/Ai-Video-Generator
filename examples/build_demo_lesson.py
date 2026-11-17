#!/usr/bin/env python
"""Build deterministic demo planning artifacts for the integrated pipeline.

The repository has no live LLM provider adapter and no lesson-planning agent.
This script therefore uses a fixed, deterministic response queue with the real
Script Agent, Whiteboard Director Agent, Example & Visual Agent, and Scene
Packager. It writes the planning artifacts consumed by
``scripts/run_lesson_pipeline.py`` and is a pipeline demonstration fixture, not a
lesson planner.

Run from the repository root:

    python examples/build_demo_lesson.py

Outputs (overwritten):
    examples/demo_lesson/lesson_plan.json
    examples/demo_lesson/planning/<scene_id>.scene_planning.json
    examples/demo_lesson/planning/<scene_id>.visual_planning.json
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

from ai_video_generator.agents.orchestration.lesson_pipeline import (  # noqa: E402
    LessonPlanningPipeline,
)
from ai_video_generator.agents.scene_packaging.scene_packager_agent import (  # noqa: E402
    ScenePackager,
)
from ai_video_generator.agents.scene_planning.llm import (  # noqa: E402
    LLMRequest,
    LLMResponse,
)
from ai_video_generator.agents.scripting.script_agent import ScriptAgent  # noqa: E402
from ai_video_generator.agents.visual_design.visual_design_agent import (  # noqa: E402
    VisualDesignAgent,
)
from ai_video_generator.agents.whiteboard.whiteboard_director_agent import (  # noqa: E402
    WhiteboardDirectorAgent,
)
from ai_video_generator.models.lesson_plan import (  # noqa: E402
    AudienceLevel,
    LessonPlan,
    PersonalizationBrief,
    SceneBrief,
)
from ai_video_generator.models.scene_planning import (  # noqa: E402
    ScenePlanningResult,
)
from ai_video_generator.models.source import (  # noqa: E402
    PageRange,
    SourceExcerpt,
    SourceReference,
)
from ai_video_generator.models.visual_planning import (  # noqa: E402
    VisualPlanningResult,
)
from ai_video_generator.video.scene_timing import DurationLimits  # noqa: E402

OUTPUT_DIR = ROOT / "examples" / "demo_lesson"


class QueuedLLM:
    """Return pre-written JSON responses in order and record each request."""

    def __init__(self, *payloads: dict[str, Any]) -> None:
        self._payloads = list(payloads)
        self.requests: list[LLMRequest] = []

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self._payloads:
            raise AssertionError("demo LLM received more calls than expected")
        return LLMResponse(content=json.dumps(self._payloads.pop(0)))


def _lesson_plan() -> LessonPlan:
    scene_one_sources = (
        SourceExcerpt(
            source_id="src-frac-1",
            reference=SourceReference(
                document_id="demo-fractions",
                page_number=1,
                section_title="What Is a Fraction?",
            ),
            text=(
                "A fraction represents a part of a whole. The top number is the "
                "numerator. The bottom number is the denominator and shows how "
                "many equal parts the whole is divided into."
            ),
        ),
    )
    scene_two_sources = (
        SourceExcerpt(
            source_id="src-frac-2",
            reference=SourceReference(
                document_id="demo-fractions",
                page_number=2,
                section_title="Adding Like Fractions",
            ),
            text=(
                "To add fractions with the same denominator, add the numerators "
                "and keep the denominator unchanged. One fifth plus two fifths "
                "equals three fifths."
            ),
        ),
    )
    return LessonPlan(
        lesson_id="lesson-fractions-demo",
        title="Adding Fractions",
        selected_pages=(PageRange(1, 2),),
        preferences=PersonalizationBrief(
            audience_level=AudienceLevel.BEGINNER,
            language="en",
            teaching_style="cooking",
            style_guidance=(
                "Use simple kitchen-sharing analogies without changing the "
                "mathematical facts."
            ),
        ),
        scenes=(
            SceneBrief(
                scene_id="fractions-intro",
                sequence=1,
                title="What Is a Fraction?",
                objective="Explain the numerator and denominator of a fraction.",
                topics=("fractions", "numerator", "denominator"),
                target_duration_seconds=11.0,
                source_excerpts=scene_one_sources,
            ),
            SceneBrief(
                scene_id="fractions-addition",
                sequence=2,
                title="Adding Like Fractions",
                objective="Add fractions that share the same denominator.",
                topics=("fractions", "addition", "like denominators"),
                target_duration_seconds=13.0,
                source_excerpts=scene_two_sources,
            ),
        ),
    )


SCRIPT_ONE: dict[str, Any] = {
    "schema_version": "1.0",
    "scene_id": "fractions-intro",
    "language": "en",
    "segments": [
        {
            "segment_id": "seg-1",
            "text": (
                "Think of a pizza cut into equal slices. A fraction shows how "
                "many slices you have, out of the whole pizza."
            ),
            "estimated_duration_seconds": 6.0,
            "key_point_ids": ["kp-1"],
        },
        {
            "segment_id": "seg-2",
            "text": (
                "The top number is the numerator. The bottom number is the "
                "denominator, which counts the equal parts in the whole."
            ),
            "estimated_duration_seconds": 7.0,
            "key_point_ids": ["kp-2"],
        },
    ],
    "key_points": [
        {
            "key_point_id": "kp-1",
            "text": "A fraction represents a part of a whole.",
            "source_ids": ["src-frac-1"],
            "importance": "core",
        },
        {
            "key_point_id": "kp-2",
            "text": "The numerator is on top and the denominator is on the bottom.",
            "source_ids": ["src-frac-1"],
            "importance": "core",
        },
    ],
    "delivery_notes": ["Pause before naming the numerator and denominator."],
}

WHITEBOARD_ONE: dict[str, Any] = {
    "schema_version": "1.0",
    "scene_id": "fractions-intro",
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
                "text": "A fraction is part of a whole",
            },
            {
                "action_id": "act-2",
                "action_type": "write_equation",
                "segment_id": "seg-2",
                "region_id": "main",
                "order": 2,
                "text": "1/2",
            },
            {
                "action_id": "act-3",
                "action_type": "write_text",
                "segment_id": "seg-2",
                "region_id": "main",
                "order": 3,
                "text": "Numerator = top number",
            },
            {
                "action_id": "act-4",
                "action_type": "write_text",
                "segment_id": "seg-2",
                "region_id": "main",
                "order": 4,
                "text": "Denominator = bottom number",
            },
        ]
    },
    "handwriting": {
        "cues": [
            {"cue_id": "hw-1", "action_id": "act-1", "mode": "handwrite", "order": 1},
            {"cue_id": "hw-2", "action_id": "act-2", "mode": "equation", "order": 2},
            {"cue_id": "hw-3", "action_id": "act-3", "mode": "handwrite", "order": 3},
            {"cue_id": "hw-4", "action_id": "act-4", "mode": "handwrite", "order": 4},
        ]
    },
    "highlights": {
        "cues": [
            {
                "highlight_id": "hl-1",
                "target_action_id": "act-2",
                "segment_id": "seg-2",
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
    "presentation_notes": ["Reveal the picture first, then the numbers."],
}

VISUAL_ONE: dict[str, Any] = {
    "scene_id": "fractions-intro",
        "items": [
            {
                "visual_id": "vis-pizza",
                "purpose": "explain_concept",
                "kind": "analogy",
                "description": (
                    "Show a pizza split into two equal slices with one slice "
                    "highlighted as one half."
                ),
                "segment_ids": ["seg-1"],
                "key_point_ids": ["kp-1"],
                "source_ids": ["src-frac-1"],
                "exact_text": ["1/2 of a pizza"],
            }
        ],
}

SCRIPT_TWO: dict[str, Any] = {
    "schema_version": "1.0",
    "scene_id": "fractions-addition",
    "language": "en",
    "segments": [
        {
            "segment_id": "seg-1",
            "text": (
                "Now we add fractions that share the same denominator. When the "
                "slices are the same size, we simply add the numerators."
            ),
            "estimated_duration_seconds": 6.5,
            "key_point_ids": ["kp-1"],
        },
        {
            "segment_id": "seg-2",
            "text": (
                "Keep the denominator unchanged. One fifth plus two fifths "
                "equals three fifths."
            ),
            "estimated_duration_seconds": 6.0,
            "key_point_ids": ["kp-2"],
        },
    ],
    "key_points": [
        {
            "key_point_id": "kp-1",
            "text": "Fractions with the same denominator can be added directly.",
            "source_ids": ["src-frac-2"],
            "importance": "core",
        },
        {
            "key_point_id": "kp-2",
            "text": "Add the numerators and keep the denominator unchanged.",
            "source_ids": ["src-frac-2"],
            "importance": "core",
        },
    ],
    "delivery_notes": ["Emphasize that the denominator does not change."],
}

WHITEBOARD_TWO: dict[str, Any] = {
    "schema_version": "1.0",
    "scene_id": "fractions-addition",
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
                "text": "Same denominator: add the numerators",
            },
            {
                "action_id": "act-2",
                "action_type": "write_equation",
                "segment_id": "seg-2",
                "region_id": "main",
                "order": 2,
                "text": "1/5 + 2/5 = 3/5",
            },
            {
                "action_id": "act-3",
                "action_type": "write_text",
                "segment_id": "seg-2",
                "region_id": "main",
                "order": 3,
                "text": "Keep the denominator",
            },
        ]
    },
    "handwriting": {
        "cues": [
            {"cue_id": "hw-1", "action_id": "act-1", "mode": "handwrite", "order": 1},
            {"cue_id": "hw-2", "action_id": "act-2", "mode": "equation", "order": 2},
            {"cue_id": "hw-3", "action_id": "act-3", "mode": "handwrite", "order": 3},
        ]
    },
    "highlights": {
        "cues": [
            {
                "highlight_id": "hl-1",
                "target_action_id": "act-2",
                "segment_id": "seg-2",
                "style": "underline",
                "order": 1,
            }
        ]
    },
    "continuity": {
        "mode": "continue_board",
        "carry_over_action_ids": ["act-4"],
        "clear_region_ids": [],
        "notes": ["Keep the numerator/denominator labels from the previous scene."],
    },
    "presentation_notes": ["Work the example left to right."],
}

VISUAL_TWO: dict[str, Any] = {
    "scene_id": "fractions-addition",
    "items": [
        {
            "visual_id": "vis-shared-slices",
            "purpose": "show_process",
            "kind": "diagram",
            "description": (
                "Show two fifths being combined into three fifths while the "
                "denominator stays the same."
            ),
            "segment_ids": ["seg-1", "seg-2"],
            "key_point_ids": ["kp-1", "kp-2"],
            "source_ids": ["src-frac-2"],
            "exact_text": ["denominator stays 5"],
        }
    ],
}


def main() -> int:
    lesson_plan = _lesson_plan()
    llm = QueuedLLM(
        SCRIPT_ONE,
        WHITEBOARD_ONE,
        VISUAL_ONE,
        SCRIPT_TWO,
        WHITEBOARD_TWO,
        VISUAL_TWO,
    )
    planning = LessonPlanningPipeline(
        ScriptAgent(llm),
        WhiteboardDirectorAgent(llm),
        VisualDesignAgent(llm),
        ScenePackager(),
        DurationLimits(minimum_seconds=5.0, maximum_seconds=120.0),
    )
    packages = planning.plan_lesson(lesson_plan)

    planning_dir = OUTPUT_DIR / "planning"
    planning_dir.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "lesson_plan.json").write_text(
        json.dumps(lesson_plan.to_dict(), indent=2) + "\n", encoding="utf-8"
    )
    for package, brief in zip(packages, lesson_plan.scenes):
        planning_result = ScenePlanningResult(
            lesson_id=lesson_plan.lesson_id,
            scene=brief,
            script=package.script,
            whiteboard=package.whiteboard,
        )
        visual_result = VisualPlanningResult(
            scene_id=package.scene_id,
            plan=package.visual,
        )
        (planning_dir / f"{package.scene_id}.scene_planning.json").write_text(
            json.dumps(planning_result.to_dict(), indent=2) + "\n", encoding="utf-8"
        )
        (planning_dir / f"{package.scene_id}.visual_planning.json").write_text(
            json.dumps(visual_result.to_dict(), indent=2) + "\n", encoding="utf-8"
        )

    print(
        json.dumps(
            {
                "status": "ok",
                "lesson_plan": str(OUTPUT_DIR / "lesson_plan.json"),
                "planning_dir": str(planning_dir),
                "scenes": [
                    {
                        "scene_id": package.scene_id,
                        "planned_duration_seconds": (
                            package.timeline.total_duration_seconds
                        ),
                    }
                    for package in packages
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
