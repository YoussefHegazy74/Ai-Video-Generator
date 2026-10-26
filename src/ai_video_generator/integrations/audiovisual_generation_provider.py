"""Single audiovisual generation provider contract.

Provider capabilities are explicit. A provider that cannot produce synchronized
audio/video is rejected by the generation service rather than silently assembled
without narration. Requested duration and measured duration are never conflated.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from ai_video_generator.core.constants import TextRenderMode
from ai_video_generator.video.scene_contract import SceneSpec


class ProviderCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    generates_synchronized_audio: bool
    supports_duration_control: bool
    supported_durations_seconds: list[float] | None = None
    min_duration_seconds: float | None = Field(default=None, gt=0)
    max_duration_seconds: float | None = Field(default=None, gt=0)
    supports_separate_audio: bool = False
    supports_idempotency_key: bool = False
    supports_cancellation: bool = False
    output_container: str = "mp4"
    text_render_mode: TextRenderMode = TextRenderMode.UNKNOWN
    requires_credentials: bool = False
    is_local: bool = True
    notes: list[str] = Field(default_factory=list)


class DurationSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_seconds: float = Field(gt=0)
    selected_seconds: float = Field(gt=0)
    duration_control: bool
    note: str | None = None


class GenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    job_id: str
    scene: SceneSpec
    requested_duration_seconds: float = Field(gt=0)
    selected_duration_seconds: float = Field(gt=0)
    scene_count: int = Field(ge=1)
    output_dir: Path
    timeout_seconds: float = Field(gt=0)
    idempotency_key: str


class GeneratedClip(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    video_path: Path
    separate_audio_path: Path | None = None
    manifest_path: Path | None = None
    provider: str
    model: str | None = None
    request_id: str | None = None
    text_render_mode: TextRenderMode = TextRenderMode.UNKNOWN
    renderer: str | None = None
    narration_sha256: str | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


@runtime_checkable
class AudiovisualGenerationProvider(Protocol):
    capabilities: ProviderCapabilities

    def check_ready(self) -> None:
        """Validate credentials/tools without making a paid generation call."""

    def resolve_duration(self, requested_seconds: float) -> DurationSelection:
        """Map a target to a provider-supported duration and explain the choice."""

    def generate(self, request: GenerationRequest) -> GeneratedClip:
        """Generate one scene; raise a typed ProviderError on failure."""
