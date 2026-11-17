from __future__ import annotations

from pathlib import Path

import pytest

from ai_video_generator.core.exceptions import ConfigurationError
from ai_video_generator.core.settings import load_settings


def _write_config(
    root: Path,
    *,
    settings_payload: str = "{}\n",
    video_payload: str = "{}\n",
) -> Path:
    config = root / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "settings.yaml").write_text(settings_payload, encoding="utf-8")
    (config / "video_generation.yaml").write_text(video_payload, encoding="utf-8")
    return config


def test_environment_overrides_yaml_and_paths_resolve_against_project_root(
    tmp_path: Path,
) -> None:
    config = _write_config(
        tmp_path,
        settings_payload=(
            "log_level: WARNING\n"
            "storage:\n"
            "  root: runtime/artifacts\n"
        ),
        video_payload=(
            "validation:\n"
            "  min_width: 500\n"
        ),
    )
    settings = load_settings(
        config,
        project_root=tmp_path,
        env={
            "AIVG_LOG_LEVEL": "debug",
            "AIVG_VALIDATION_MIN_WIDTH": "800",
            "AIVG_GENERATION_MAX_ATTEMPTS": "2",
            "GEMINI_API_KEY": "super-secret-value",
        },
        load_dotenv_file=False,
    )

    assert settings.log_level == "DEBUG"
    assert settings.validation.min_width == 800
    assert settings.generation.max_attempts == 2
    assert settings.storage_root == (tmp_path / "runtime" / "artifacts").resolve()
    assert settings.temp_root == settings.storage_root / ".work"
    assert settings.registry_path == settings.storage_root / "artifact_registry.json"

    assert settings.provider.gemini_api_key is not None
    assert (
        settings.provider.gemini_api_key.get_secret_value() == "super-secret-value"
    )
    assert "gemini_api_key" not in settings.model_dump()
    assert "gemini_api_key" not in settings.public_dict()
    assert "super-secret-value" not in repr(settings)


def test_invalid_boolean_environment_value_is_rejected(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    with pytest.raises(ConfigurationError) as error:
        load_settings(
            config,
            project_root=tmp_path,
            env={"AIVG_JSON_LOGS": "sometimes"},
            load_dotenv_file=False,
        )
    assert error.value.details["variable"] == "AIVG_JSON_LOGS"


def test_invalid_provider_environment_value_is_rejected(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    with pytest.raises(ConfigurationError) as error:
        load_settings(
            config,
            project_root=tmp_path,
            env={"AIVG_PROVIDER": "not-a-provider"},
            load_dotenv_file=False,
        )
    assert any(
        "provider" in str(item.get("loc", ""))
        for item in error.value.details["errors"]
    )


def test_custom_config_directory_requires_both_yaml_files(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.yaml").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as error:
        load_settings(
            config,
            project_root=tmp_path,
            env={},
            load_dotenv_file=False,
        )
    assert "video_generation.yaml" in error.value.message


def test_invalid_yaml_schema_is_reported_as_configuration_error(tmp_path: Path) -> None:
    config = _write_config(
        tmp_path,
        video_payload=(
            "validation:\n"
            "  min_scene_duration_seconds: 20\n"
            "  max_scene_duration_seconds: 10\n"
        ),
    )
    with pytest.raises(ConfigurationError) as error:
        load_settings(
            config,
            project_root=tmp_path,
            env={},
            load_dotenv_file=False,
        )
    assert error.value.code == "configuration_error"
    assert error.value.details["errors"]
