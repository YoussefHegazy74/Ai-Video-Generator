"""End-to-end lesson pipeline: planning, packaging, adaptation, and runtime.

Responsibility: coordinate the implemented agents and services for one lesson so
the planning stack and the media stack execute as a single deterministic flow.
Workflow: ``LessonPlan`` -> Script Agent -> Whiteboard Director ->
Example & Visual Agent -> Scene Packager -> runtime adapter ->
generate/validate/assemble orchestrator -> final video.
Inputs: a validated ``LessonPlan``, injected planning agents, a configured
``DurationLimits`` policy, and an injected runtime orchestrator.
Outputs: ``LessonPipelineResult`` with the packaged scenes, the runtime
``SceneSet``, and the terminal ``GenerationJob``.
Interacts with: ``agents.scripting``, ``agents.whiteboard``,
``agents.visual_design``, ``agents.scene_packaging``,
``agents.orchestration.workflow_orchestrator``, and ``video.generation_contract``.

Each agent and service is injected. This module owns ordering, the whiteboard
continuity chain, and the planning-to-runtime handoff; it does not implement
prompting, rendering, validation policy, or media work itself. Typed failures
from the agents, packager, adapter, and orchestrator propagate unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ...models.lesson_plan import LessonPlan
from ...models.scene import ScenePlanningInput
from ...models.scene_planning import ScenePlanningResult
from ...models.visual_planning import VisualPlanningInput
from ...video.generation_contract import SceneSet
from ...video.scene_contract import ScenePackage
from ...video.scene_timing import DurationLimits
from ...models.generation_job import GenerationJob
from ..scene_packaging.scene_packager_agent import ScenePackager
from ..scripting.script_agent import ScriptAgent
from ..visual_design.visual_design_agent import VisualDesignAgent
from ..whiteboard.whiteboard_director_agent import WhiteboardDirectorAgent
from .runtime_adapter import RuntimeSceneOptions, SceneRuntimeAdapter


class LessonRuntimeRunner(Protocol):
    """Minimal interface required from the media workflow orchestrator."""

    def run(self, scene_set: SceneSet, *, job_id: str | None = None) -> GenerationJob:
        ...


@dataclass(frozen=True, slots=True)
class LessonPipelineResult:
    """Complete handoff from one integrated lesson run."""

    lesson_id: str
    packages: tuple[ScenePackage, ...]
    scene_set: SceneSet
    job: GenerationJob

    def __post_init__(self) -> None:
        if not self.packages:
            raise ValueError("packages must contain at least one scene package")
        if self.scene_set.project_id != self.lesson_id:
            raise ValueError(
                "scene_set.project_id must match the lesson_id it was built from"
            )
        if len(self.packages) != len(self.scene_set.scenes):
            raise ValueError(
                "packages and scene_set.scenes must contain the same number of "
                "scenes"
            )


class LessonPlanningPipeline:
    """Run script, whiteboard, visual, and packaging stages for one lesson."""

    def __init__(
        self,
        script_agent: ScriptAgent,
        whiteboard_agent: WhiteboardDirectorAgent,
        visual_agent: VisualDesignAgent,
        packager: ScenePackager,
        duration_limits: DurationLimits,
    ) -> None:
        if not isinstance(duration_limits, DurationLimits):
            raise TypeError("duration_limits must be a DurationLimits")
        self.script_agent = script_agent
        self.whiteboard_agent = whiteboard_agent
        self.visual_agent = visual_agent
        self.packager = packager
        self.duration_limits = duration_limits

    def plan_lesson(self, lesson_plan: LessonPlan) -> tuple[ScenePackage, ...]:
        """Plan and package every scene in lesson order.

        The first scene receives ``previous_plan=None``; every later scene
        receives the previous whiteboard plan so continuity can be validated.
        """
        if not isinstance(lesson_plan, LessonPlan):
            raise TypeError("lesson_plan must be a LessonPlan")

        packages: list[ScenePackage] = []
        previous_plan = None
        for brief in lesson_plan.scenes:
            planning_input = ScenePlanningInput.from_lesson_plan(
                lesson_plan, brief.scene_id
            )
            script = self.script_agent.generate(planning_input)
            whiteboard = self.whiteboard_agent.generate_for_scene(
                planning_input,
                script,
                previous_plan=previous_plan,
            )
            visual_result = self.visual_agent.generate(
                VisualPlanningInput(
                    planning_input=planning_input,
                    script=script,
                    whiteboard=whiteboard,
                )
            )
            package = self.packager.package(
                ScenePlanningResult(
                    lesson_id=lesson_plan.lesson_id,
                    scene=brief,
                    script=script,
                    whiteboard=whiteboard,
                ),
                visual_result,
                self.duration_limits,
            )
            packages.append(package)
            previous_plan = whiteboard

        return tuple(packages)


class LessonPipeline:
    """Integrated lesson-to-video pipeline over the implemented components."""

    def __init__(
        self,
        planning: LessonPlanningPipeline,
        adapter: SceneRuntimeAdapter,
        runtime: LessonRuntimeRunner,
        *,
        options: RuntimeSceneOptions | None = None,
    ) -> None:
        self.planning = planning
        self.adapter = adapter
        self.runtime = runtime
        self.options = options or RuntimeSceneOptions()

    def plan(self, lesson_plan: LessonPlan) -> tuple[ScenePackage, ...]:
        """Run planning and packaging only, without generating media."""
        return self.planning.plan_lesson(lesson_plan)

    def run(
        self,
        lesson_plan: LessonPlan,
        *,
        job_id: str | None = None,
    ) -> LessonPipelineResult:
        """Plan, package, adapt, generate, validate, and assemble one lesson."""
        packages = self.planning.plan_lesson(lesson_plan)
        scene_set = self.adapter.adapt_lesson(
            packages, lesson_plan, options=self.options
        )
        job = self.runtime.run(scene_set, job_id=job_id)
        return LessonPipelineResult(
            lesson_id=lesson_plan.lesson_id,
            packages=packages,
            scene_set=scene_set,
            job=job,
        )


__all__ = [
    "LessonPipeline",
    "LessonPipelineResult",
    "LessonPlanningPipeline",
    "LessonRuntimeRunner",
]
