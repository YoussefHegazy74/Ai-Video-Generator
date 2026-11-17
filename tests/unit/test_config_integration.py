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

import sys
from pathlib import Path

import pytest

from ai_video_generator.core.exceptions import (
    ConfigurationError,
    ProviderCapabilityError,
)
from ai_video_generator.core.settings import load_settings
from ai_video_generator.integrations.gemini_llm_client import GeminiLLMClient
from ai_video_generator.integrations.provider_factory import (
    build_llm_client,
    build_provider,
)
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
    assert settings.llm.provider == "gemini"
    assert settings.llm.model == "gemini-3.8-flash"
    assert settings.llm.max_attempts == 3
    assert settings.llm.max_output_tokens == 8192


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


def test_llm_environment_overrides_reach_settings():
    settings = _settings(
        {
            "AIVG_LLM_MODEL": "gemini-test-model",
            "AIVG_LLM_TEMPERATURE": "0.4",
            "AIVG_LLM_MAX_ATTEMPTS": "5",
            "AIVG_LLM_TIMEOUT_SECONDS": "45",
        }
    )

    assert settings.llm.model == "gemini-test-model"
    assert settings.llm.temperature == 0.4
    assert settings.llm.max_attempts == 5
    assert settings.llm.timeout_seconds == 45.0


def test_unsupported_llm_provider_is_rejected():
    with pytest.raises(ConfigurationError):
        _settings({"AIVG_LLM_PROVIDER": "openai"})


def test_planning_factory_requires_credentials_when_no_client_is_injected():
    settings = _settings()

    with pytest.raises(ConfigurationError, match="GEMINI_API_KEY"):
        build_planning_pipeline(settings)


def test_planning_factory_fails_fast_when_the_sdk_is_missing(monkeypatch):
    settings = _settings({"GEMINI_API_KEY": "test-key"})
    monkeypatch.setitem(sys.modules, "google", None)
    monkeypatch.setitem(sys.modules, "google.genai", None)

    with pytest.raises(ConfigurationError, match="google-genai"):
        build_planning_pipeline(settings)


def test_build_llm_client_uses_configured_model_and_injected_transport():
    settings = _settings()
    sdk_client = object()

    client = build_llm_client(settings, client=sdk_client)

    assert isinstance(client, GeminiLLMClient)
    assert client.model == "gemini-3.8-flash"
    assert client.max_attempts == 3


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
