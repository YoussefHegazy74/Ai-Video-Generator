"""Tests for the integrated planning pipeline and lesson orchestration.

Responsibility: verify agent ordering, the whiteboard continuity chain, and the
planning-to-runtime handoff without media work.
Workflow: unit tests over deterministic fake LLM responses.
Inputs: conftest planning fixtures.
Outputs: assertions on packaged scenes and the runtime call.
Interacts with: ``agents.orchestration.lesson_pipeline`` and the planning agents.
"""

from __future__ import annotations

import copy
from dataclasses import replace

from ai_video_generator.agents.orchestration.lesson_pipeline import (
    LessonPipeline,
    LessonPlanningPipeline,
)
from ai_video_generator.agents.orchestration.runtime_adapter import (
    SceneRuntimeAdapter,
)
from ai_video_generator.agents.scene_packaging.scene_packager_agent import (
    ScenePackager,
)
from ai_video_generator.agents.scripting.script_agent import ScriptAgent
from ai_video_generator.agents.visual_design.visual_design_agent import (
    VisualDesignAgent,
)
from ai_video_generator.agents.whiteboard.whiteboard_director_agent import (
    WhiteboardDirectorAgent,
)
from ai_video_generator.models.lesson_plan import LessonPlan
from ai_video_generator.video.scene_timing import DurationLimits

LIMITS = DurationLimits(minimum_seconds=5.0, maximum_seconds=120.0)


def _visual_payload(scene_id: str) -> dict:
    return {
        "scene_id": scene_id,
        "items": [
            {
                "visual_id": "vis-1",
                "purpose": "explain_concept",
                "kind": "diagram",
                "description": "Show how layers pass information forward.",
                "segment_ids": ["seg-1"],
                "key_point_ids": ["kp-1"],
                "source_ids": ["src-1"],
                "exact_text": ["Layers"],
            }
        ],
    }


def _lesson_plan(planning_input, scene_ids: tuple[str, ...] = ("scene-001",)) -> LessonPlan:
    briefs = tuple(
        replace(
            planning_input.scene,
            scene_id=scene_id,
            sequence=index + 1,
        )
        for index, scene_id in enumerate(scene_ids)
    )
    return LessonPlan(
        lesson_id=planning_input.lesson_id,
        title="Neural Networks",
        selected_pages=planning_input.selected_pages,
        preferences=planning_input.preferences,
        scenes=briefs,
    )


def _payloads_for(
    scene_id: str,
    script_payload: dict,
    whiteboard_payload: dict,
    *,
    continuity: str = "fresh_board",
) -> tuple[dict, dict, dict]:
    script = copy.deepcopy(script_payload)
    script["scene_id"] = scene_id
    whiteboard = copy.deepcopy(whiteboard_payload)
    whiteboard["scene_id"] = scene_id
    whiteboard["continuity"]["mode"] = continuity
    return script, whiteboard, _visual_payload(scene_id)


class _RecordingWhiteboardAgent:
    def __init__(self, inner: WhiteboardDirectorAgent) -> None:
        self.inner = inner
        self.previous_plans: list[object] = []

    def generate_for_scene(self, planning_input, script, *, previous_plan=None):
        self.previous_plans.append(previous_plan)
        return self.inner.generate_for_scene(
            planning_input, script, previous_plan=previous_plan
        )


class _StubRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[object, str | None]] = []

    def run(self, scene_set, *, job_id=None):
        self.calls.append((scene_set, job_id))
        return object()


def test_planning_pipeline_orders_agents_and_packages_every_scene(
    planning_input, script_payload, whiteboard_payload, fake_llm_factory
):
    payloads = _payloads_for("scene-001", script_payload, whiteboard_payload)
    llm = fake_llm_factory(*[copy.deepcopy(payload) for payload in payloads])
    planning = LessonPlanningPipeline(
        ScriptAgent(llm),
        WhiteboardDirectorAgent(llm),
        VisualDesignAgent(llm),
        ScenePackager(),
        LIMITS,
    )

    packages = planning.plan_lesson(_lesson_plan(planning_input))

    assert [package.scene_id for package in packages] == ["scene-001"]
    package = packages[0]
    assert package.lesson_id == "lesson-001"
    assert package.script.narration.startswith("A neural network is like")
    assert package.visual.items[0].exact_text == ("Layers",)
    assert package.timeline.total_duration_seconds == 30.0
    assert [request.response_schema_name for request in llm.requests] == [
        "SceneScript",
        "WhiteboardPlan",
        "VisualPlan",
    ]


def test_planning_pipeline_passes_the_previous_whiteboard_plan(
    planning_input, script_payload, whiteboard_payload, fake_llm_factory
):
    first = _payloads_for("scene-001", script_payload, whiteboard_payload)
    second = _payloads_for(
        "scene-002", script_payload, whiteboard_payload, continuity="continue_board"
    )
    # The second whiteboard plan must carry an action from the first plan.
    second[1]["continuity"]["carry_over_action_ids"] = ["act-4"]
    second[1]["continuity"]["clear_region_ids"] = []
    second[1]["drawing"]["actions"].append(
        {
            "action_id": "act-5",
            "action_type": "write_text",
            "segment_id": "seg-3",
            "region_id": "summary",
            "order": 5,
            "text": "Keep the previous summary",
        }
    )
    second[1]["handwriting"]["cues"].append(
        {"cue_id": "hw-4", "action_id": "act-5", "mode": "handwrite", "order": 4}
    )
    second[1]["highlights"]["cues"] = [
        {
            "highlight_id": "hl-1",
            "target_action_id": "act-5",
            "segment_id": "seg-3",
            "style": "marker",
            "order": 1,
        }
    ]
    llm = fake_llm_factory(
        *[copy.deepcopy(payload) for payload in first],
        *[copy.deepcopy(payload) for payload in second],
    )
    whiteboard_agent = _RecordingWhiteboardAgent(WhiteboardDirectorAgent(llm))
    planning = LessonPlanningPipeline(
        ScriptAgent(llm),
        whiteboard_agent,
        VisualDesignAgent(llm),
        ScenePackager(),
        LIMITS,
    )

    packages = planning.plan_lesson(
        _lesson_plan(planning_input, ("scene-001", "scene-002"))
    )

    assert [package.scene_id for package in packages] == ["scene-001", "scene-002"]
    assert whiteboard_agent.previous_plans[0] is None
    assert (
        whiteboard_agent.previous_plans[1].scene_id
        == packages[0].whiteboard.scene_id
    )
    assert (
        whiteboard_agent.previous_plans[1]
        is packages[0].whiteboard
    )


def test_lesson_pipeline_hands_the_adapted_scene_set_to_the_runtime(
    planning_input, script_payload, whiteboard_payload, fake_llm_factory
):
    payloads = _payloads_for("scene-001", script_payload, whiteboard_payload)
    llm = fake_llm_factory(*[copy.deepcopy(payload) for payload in payloads])
    planning = LessonPlanningPipeline(
        ScriptAgent(llm),
        WhiteboardDirectorAgent(llm),
        VisualDesignAgent(llm),
        ScenePackager(),
        LIMITS,
    )
    runtime = _StubRuntime()
    pipeline = LessonPipeline(planning, SceneRuntimeAdapter(), runtime)

    result = pipeline.run(_lesson_plan(planning_input), job_id="job-1")

    assert result.lesson_id == "lesson-001"
    assert result.scene_set.project_id == "lesson-001"
    assert result.scene_set.scenes[0].narration.startswith("A neural network is like")
    assert len(runtime.calls) == 1
    scene_set, job_id = runtime.calls[0]
    assert job_id == "job-1"
    assert scene_set is result.scene_set


def test_lesson_pipeline_propagates_runtime_failures(
    planning_input, script_payload, whiteboard_payload, fake_llm_factory
):
    import pytest

    payloads = _payloads_for("scene-001", script_payload, whiteboard_payload)
    llm = fake_llm_factory(*[copy.deepcopy(payload) for payload in payloads])
    planning = LessonPlanningPipeline(
        ScriptAgent(llm),
        WhiteboardDirectorAgent(llm),
        VisualDesignAgent(llm),
        ScenePackager(),
        LIMITS,
    )

    class FailingRuntime:
        def run(self, scene_set, *, job_id=None):
            raise RuntimeError("media stage failed")

    pipeline = LessonPipeline(planning, SceneRuntimeAdapter(), FailingRuntime())

    with pytest.raises(RuntimeError, match="media stage failed"):
        pipeline.run(_lesson_plan(planning_input))
