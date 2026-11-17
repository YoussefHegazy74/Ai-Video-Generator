"""Tests for integrated configuration and provider wiring.

Responsibility: verify that the shipped configuration resolves into the
planning/media factories consistently, that documented environment overrides
work, and that missing credentials fail with a clear typed error.
Workflow: configuration and factory construction only; no media is generated.
Inputs: the repository ``config`` directory and explicit environment mappings.
Outputs: assertions on settings, pipeline components, and provider readiness.
Interacts with: ``core.settings``, ``services.pipeline_factory``, and providers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_video_generator.core.exceptions import (
    ConfigurationError,
    ProviderCapabilityError,
)
from ai_video_generator.core.settings import load_settings
from ai_video_generator.integrations.provider_factory import build_provider
from ai_video_generator.services.pipeline_factory import (
    build_planning_pipeline,
)
from ai_video_generator.video.media_probe import FFmpegTools

REPO_ROOT = Path(__file__).resolve().parents[2]


def _settings(env: dict[str, str] | None = None):
    return load_settings(
        config_dir=REPO_ROOT / "config",
        project_root=REPO_ROOT,
        env=env or {},
    )


def test_project_configuration_resolves_planning_and_validation_policy():
    settings = _settings()

    assert settings.planning.min_scene_duration_seconds == 5.0
    assert settings.planning.max_scene_duration_seconds == 120.0
    assert settings.planning.whiteboard_style == "clean_whiteboard"
    assert settings.planning.background_color == "#F8FAFC"
    assert settings.validation.min_scene_duration_seconds == 5.0
    assert settings.validation.max_scene_duration_seconds == 120.0
    assert settings.validation.require_audio is True
    assert settings.assembly.width == 1280
    assert settings.assembly.height == 720


def test_environment_overrides_reach_planning_settings():
    settings = _settings(
        {
            "AIVG_PLANNING_MAX_SCENE_DURATION_SECONDS": "90",
            "AIVG_PLANNING_WHITEBOARD_STYLE": "marker_board",
            "AIVG_PLANNING_SHOW_SCENE_TITLE": "false",
        }
    )

    assert settings.planning.max_scene_duration_seconds == 90.0
    assert settings.planning.whiteboard_style == "marker_board"
    assert settings.planning.show_scene_title is False


def test_planning_factory_uses_the_configured_duration_policy(fake_llm_factory):
    settings = _settings()
    llm = fake_llm_factory()

    components = build_planning_pipeline(settings, llm)

    assert components.duration_limits.minimum_seconds == 5.0
    assert components.duration_limits.maximum_seconds == 120.0
    assert components.runtime_options.whiteboard_style == "clean_whiteboard"
    assert components.planning.duration_limits is components.duration_limits


def test_missing_gemini_credentials_fail_with_a_clear_typed_error(tmp_path):
    settings = _settings({"AIVG_PROVIDER": "gemini_omni"})
    settings = settings.model_copy(
        update={
            "storage": settings.storage.model_copy(
                update={"root": tmp_path / "artifacts"}
            )
        }
    )
    media_tools = FFmpegTools(timeout_seconds=5)
    provider = build_provider(settings, media_tools)

    with pytest.raises(ProviderCapabilityError, match="GEMINI_API_KEY"):
        provider.check_ready()


def test_invalid_planning_duration_bounds_are_rejected():
    with pytest.raises(ConfigurationError):
        _settings(
            {
                "AIVG_PLANNING_MIN_SCENE_DURATION_SECONDS": "30",
                "AIVG_PLANNING_MAX_SCENE_DURATION_SECONDS": "10",
            }
        )
