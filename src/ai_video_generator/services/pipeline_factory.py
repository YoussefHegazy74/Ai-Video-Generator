"""Factories that wire configured components into runnable pipelines.

Responsibility: centralize construction of the planning and media pipelines from
``AppSettings`` so the CLI, tests, and future API share one wiring path.
Workflow: called once at application/command startup; dependency objects flow
into the agents and services by injection.
Inputs: validated ``AppSettings``, an ``LLMClient`` for planning, and optional
provider/progress/cancellation hooks.
Outputs: ``PlanningPipelineComponents`` and ``RuntimePipelineComponents``.
Interacts with: ``core.settings``, planning agents, the Scene Packager, the
runtime adapter, provider factory, storage, validation, assembly, and the
workflow orchestrator.

Factories make no LLM, network, or media calls. They only construct objects; the
caller decides when to run, and ``FFmpegTools.ensure_available`` remains an
explicit precondition for media execution.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from ai_video_generator.agents.assembly.video_assembly_agent import (
    VideoAssemblyAgent,
)
from ai_video_generator.agents.orchestration.lesson_pipeline import (
    LessonPlanningPipeline,
)
from ai_video_generator.agents.orchestration.runtime_adapter import (
    RuntimeSceneOptions,
)
from ai_video_generator.agents.orchestration.workflow_orchestrator import (
    CancellationCheck,
    JobProgressCallback,
    WorkflowOrchestrator,
)
from ai_video_generator.agents.scene_packaging.scene_packager_agent import (
    ScenePackager,
)
from ai_video_generator.agents.scene_planning.llm import LLMClient
from ai_video_generator.agents.scripting.script_agent import ScriptAgent
from ai_video_generator.agents.validation.scene_validation_agent import (
    SceneValidationAgent,
)
from ai_video_generator.agents.visual_design.visual_design_agent import (
    VisualDesignAgent,
)
from ai_video_generator.agents.whiteboard.whiteboard_director_agent import (
    WhiteboardDirectorAgent,
)
from ai_video_generator.core.settings import AppSettings
from ai_video_generator.integrations.audiovisual_generation_provider import (
    AudiovisualGenerationProvider,
)
from ai_video_generator.integrations.provider_factory import (
    build_llm_client,
    build_provider,
)
from ai_video_generator.services.scene_generation_service import (
    SceneGenerationService,
)
from ai_video_generator.services.storage_service import ArtifactStorageService
from ai_video_generator.services.video_assembly_service import (
    VideoAssemblyService,
)
from ai_video_generator.storage.artifact_registry import ArtifactRegistry
from ai_video_generator.storage.file_storage import LocalFileStorage
from ai_video_generator.video.assembly import FFmpegAssembler
from ai_video_generator.video.audio_video_validation import (
    SceneValidator,
)
from ai_video_generator.video.media_probe import FFmpegTools
from ai_video_generator.video.scene_timing import DurationLimits


@dataclass(frozen=True)
class PlanningPipelineComponents:
    """Planning-side agents and the deterministic packager."""

    script_agent: ScriptAgent
    whiteboard_agent: WhiteboardDirectorAgent
    visual_agent: VisualDesignAgent
    packager: ScenePackager
    planning: LessonPlanningPipeline
    duration_limits: DurationLimits
    runtime_options: RuntimeSceneOptions


@dataclass(frozen=True)
class RuntimePipelineComponents:
    """Media-side services and the synchronous workflow orchestrator."""

    settings: AppSettings
    media_tools: FFmpegTools
    storage: LocalFileStorage
    artifact_storage: ArtifactStorageService
    provider: AudiovisualGenerationProvider
    generation_service: SceneGenerationService
    validator: SceneValidator
    validation_agent: SceneValidationAgent
    assembly_service: VideoAssemblyService
    assembly_agent: VideoAssemblyAgent
    orchestrator: WorkflowOrchestrator


def build_planning_pipeline(
    settings: AppSettings,
    llm_client: LLMClient | None = None,
) -> PlanningPipelineComponents:
    """Construct the planning agents, packager, and duration policy.

    When ``llm_client`` is omitted, the configured Gemini adapter is built and
    missing credentials raise ``ConfigurationError`` immediately. Tests inject a
    fake client, which keeps the public agent interfaces unchanged.
    """
    if llm_client is None:
        configured_client = build_llm_client(settings)
        configured_client.check_ready()
        client: LLMClient = configured_client
    else:
        client = llm_client
    temperature = settings.llm.temperature
    script_agent = ScriptAgent(client, temperature=temperature)
    whiteboard_agent = WhiteboardDirectorAgent(client, temperature=temperature)
    visual_agent = VisualDesignAgent(client, temperature=temperature)
    packager = ScenePackager()
    duration_limits = DurationLimits(
        minimum_seconds=settings.planning.min_scene_duration_seconds,
        maximum_seconds=settings.planning.max_scene_duration_seconds,
        tolerance_seconds=settings.planning.duration_tolerance_seconds,
    )
    runtime_options = RuntimeSceneOptions(
        whiteboard_style=settings.planning.whiteboard_style,
        background_color=settings.planning.background_color,
        accent_color=settings.planning.accent_color,
        show_scene_title=settings.planning.show_scene_title,
    )
    planning = LessonPlanningPipeline(
        script_agent,
        whiteboard_agent,
        visual_agent,
        packager,
        duration_limits,
    )
    return PlanningPipelineComponents(
        script_agent=script_agent,
        whiteboard_agent=whiteboard_agent,
        visual_agent=visual_agent,
        packager=packager,
        planning=planning,
        duration_limits=duration_limits,
        runtime_options=runtime_options,
    )


def build_runtime_pipeline(
    settings: AppSettings,
    *,
    provider: AudiovisualGenerationProvider | None = None,
    progress_callback: JobProgressCallback | None = None,
    cancellation_check: CancellationCheck | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> RuntimePipelineComponents:
    """Construct storage, provider, generation, validation, and assembly."""
    media_tools = FFmpegTools(
        settings.media.ffmpeg_path,
        settings.media.ffprobe_path,
        timeout_seconds=settings.media.probe_timeout_seconds,
    )
    storage = LocalFileStorage(
        settings.storage_root, temp_root=settings.temp_root
    )
    artifact_storage = ArtifactStorageService(
        storage, ArtifactRegistry(settings.registry_path)
    )
    active_provider = provider or build_provider(settings, media_tools)

    generation_service = SceneGenerationService(
        active_provider,
        artifact_storage,
        media_tools,
        max_attempts=settings.generation.max_attempts,
        timeout_seconds=settings.generation.timeout_seconds,
        backoff_base_seconds=settings.generation.backoff_base_seconds,
        backoff_max_seconds=settings.generation.backoff_max_seconds,
        backoff_jitter_seconds=settings.generation.backoff_jitter_seconds,
        sync_tolerance_seconds=settings.generation.sync_tolerance_seconds,
        sleep=sleep,
    )
    validator = SceneValidator(
        media_tools,
        storage,
        policy=settings.validation,
    )
    validation_agent = SceneValidationAgent(validator, artifact_storage)
    assembler = FFmpegAssembler(media_tools, settings.assembly)
    assembly_service = VideoAssemblyService(
        assembler,
        artifact_storage,
        media_tools,
        audio_silence_mean_db=settings.validation.audio_silence_mean_db,
        audio_silence_max_db=settings.validation.audio_silence_max_db,
    )
    assembly_agent = VideoAssemblyAgent(assembly_service)
    orchestrator = WorkflowOrchestrator(
        generation_service,
        validation_agent,
        assembly_service,
        artifact_storage,
        max_validation_attempts=settings.generation.max_validation_attempts,
        progress_callback=progress_callback,
        cancellation_check=cancellation_check,
    )
    return RuntimePipelineComponents(
        settings=settings,
        media_tools=media_tools,
        storage=storage,
        artifact_storage=artifact_storage,
        provider=active_provider,
        generation_service=generation_service,
        validator=validator,
        validation_agent=validation_agent,
        assembly_service=assembly_service,
        assembly_agent=assembly_agent,
        orchestrator=orchestrator,
    )


__all__ = [
    "PlanningPipelineComponents",
    "RuntimePipelineComponents",
    "build_planning_pipeline",
    "build_runtime_pipeline",
]
