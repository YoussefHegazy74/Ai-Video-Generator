"""Configuration-driven construction of audiovisual providers.

The factory never makes a paid call. It only constructs an adapter and optional
local tools; callers must invoke ``check_ready`` before generation.
"""

from __future__ import annotations

from typing import Any

from ai_video_generator.core.exceptions import ConfigurationError
from ai_video_generator.core.settings import AppSettings
from ai_video_generator.integrations.audiovisual_generation_provider import (
    AudiovisualGenerationProvider,
    ProviderCapabilities,
)
from ai_video_generator.integrations.deterministic_whiteboard_provider import (
    DeterministicWhiteboardProvider,
)
from ai_video_generator.integrations.gemini_omni_provider import GeminiOmniProvider
from ai_video_generator.integrations.narration_synthesizer import (
    AutoNarrator,
    FliteNarrator,
    NarrationSynthesizer,
    WindowsSapiNarrator,
)
from ai_video_generator.video.media_probe import FFmpegTools


def build_narrator(settings: AppSettings) -> NarrationSynthesizer:
    whiteboard = settings.provider.whiteboard
    if whiteboard.narrator == "auto":
        return AutoNarrator(
            ffmpeg_path=settings.media.ffmpeg_path,
            rate=whiteboard.tts_rate,
        )
    if whiteboard.narrator == "windows_sapi":
        return WindowsSapiNarrator(rate=whiteboard.tts_rate)
    if whiteboard.narrator == "ffmpeg_flite":
        return FliteNarrator(ffmpeg_path=settings.media.ffmpeg_path)
    raise ConfigurationError(
        f"Unsupported narrator configuration: {whiteboard.narrator!r}",
        details={"narrator": whiteboard.narrator},
    )


def build_provider(
    settings: AppSettings,
    media_tools: FFmpegTools,
    *,
    narrator: NarrationSynthesizer | None = None,
) -> AudiovisualGenerationProvider:
    provider_id = settings.provider.id
    if provider_id == "deterministic_whiteboard":
        whiteboard = settings.provider.whiteboard
        return DeterministicWhiteboardProvider(
            media_tools=media_tools,
            narrator=narrator or build_narrator(settings),
            width=whiteboard.width,
            height=whiteboard.height,
            fps=whiteboard.fps,
            preset=whiteboard.preset,
            crf=whiteboard.crf,
        )
    if provider_id == "gemini_omni":
        gemini = settings.provider.gemini
        api_key = (
            settings.provider.gemini_api_key.get_secret_value()
            if settings.provider.gemini_api_key is not None
            else None
        )
        return GeminiOmniProvider(
            api_key=api_key,
            model=gemini.model,
            aspect_ratio=gemini.aspect_ratio,
            resolution=gemini.resolution,
        )
    raise ConfigurationError(
        f"Unsupported provider: {provider_id!r}",
        details={"provider": provider_id},
    )


def capabilities_dict(provider: AudiovisualGenerationProvider) -> dict[str, Any]:
    capabilities: ProviderCapabilities = provider.capabilities
    return capabilities.model_dump(mode="json")


__all__ = ["build_narrator", "build_provider", "capabilities_dict"]
