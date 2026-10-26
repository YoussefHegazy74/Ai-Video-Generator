"""Typed application settings loaded from YAML and ``AIVG_*`` environment variables.

Precedence, from lowest to highest:

1. Model defaults in this module.
2. ``config/settings.yaml``.
3. ``config/video_generation.yaml`` (generation/validation/assembly sections).
4. Explicit ``AIVG_*`` environment variables.

Relative paths are resolved against the repository root, not the process working
directory. ``GEMINI_API_KEY`` is the only credential read here; it is stored in a
Pydantic ``SecretStr``, excluded from serialization and repr, and never logged.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)

from ai_video_generator.core.exceptions import ConfigurationError
from ai_video_generator.video.assembly import AssemblyOptions
from ai_video_generator.video.audio_video_validation import ValidationPolicy

_SUPPORTED_LOG_LEVELS = {
    "CRITICAL",
    "ERROR",
    "WARNING",
    "INFO",
    "DEBUG",
    "NOTSET",
}
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


class StorageSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: Path = Path("artifacts")
    temp_root: Path | None = None
    registry_path: Path | None = None


class MediaSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ffmpeg_path: str = Field(default="ffmpeg", min_length=1)
    ffprobe_path: str = Field(default="ffprobe", min_length=1)
    probe_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    render_timeout_seconds: float = Field(default=1800.0, gt=0, le=7200)


class GeminiSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(default="gemini-omni-1.1-flash", min_length=1)
    aspect_ratio: str = "16:9"
    resolution: str = "720p"

    @field_validator("aspect_ratio")
    @classmethod
    def _valid_aspect_ratio(cls, value: str) -> str:
        if value not in {"16:9", "9:16"}:
            raise ValueError("Gemini aspect ratio must be '16:9' or '9:16'")
        return value

    @field_validator("resolution")
    @classmethod
    def _valid_resolution(cls, value: str) -> str:
        if value not in {"360p", "720p", "1080p", "4K"}:
            raise ValueError("Gemini resolution must be 360p, 720p, 1080p, or 4K")
        return value


class WhiteboardSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: int = Field(default=1280, ge=160, le=3840)
    height: int = Field(default=720, ge=90, le=2160)
    fps: int = Field(default=24, ge=1, le=60)
    preset: str = Field(default="veryfast", min_length=1)
    crf: int = Field(default=20, ge=0, le=51)
    narrator: str = "auto"
    tts_rate: int = Field(default=0, ge=-10, le=10)

    @field_validator("narrator")
    @classmethod
    def _valid_narrator(cls, value: str) -> str:
        if value not in {"auto", "windows_sapi", "ffmpeg_flite"}:
            raise ValueError(
                "narrator must be one of: auto, windows_sapi, ffmpeg_flite"
            )
        return value


class ProviderSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = "deterministic_whiteboard"
    gemini: GeminiSettings = Field(default_factory=GeminiSettings)
    whiteboard: WhiteboardSettings = Field(default_factory=WhiteboardSettings)
    gemini_api_key: SecretStr | None = Field(
        default=None,
        exclude=True,
        repr=False,
        description="Read only from GEMINI_API_KEY; excluded from all dumps.",
    )

    @field_validator("id")
    @classmethod
    def _valid_provider(cls, value: str) -> str:
        if value not in {"deterministic_whiteboard", "gemini_omni"}:
            raise ValueError(
                "provider.id must be 'deterministic_whiteboard' or 'gemini_omni'"
            )
        return value


class GenerationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(default=3, ge=1, le=10)
    backoff_base_seconds: float = Field(default=1.0, ge=0, le=60)
    backoff_max_seconds: float = Field(default=8.0, ge=0, le=300)
    backoff_jitter_seconds: float = Field(default=0.25, ge=0, le=10)
    timeout_seconds: float = Field(default=600.0, gt=0, le=3600)
    max_validation_attempts: int = Field(default=2, ge=1, le=5)
    sync_tolerance_seconds: float = Field(default=0.35, ge=0, le=5)

    @field_validator("backoff_max_seconds")
    @classmethod
    def _max_not_below_base(cls, value: float, info: Any) -> float:
        base = info.data.get("backoff_base_seconds")
        if base is not None and value < base:
            raise ValueError("backoff_max_seconds must be >= backoff_base_seconds")
        return value


class AppSettings(BaseModel):
    """Fully resolved runtime settings for Parts 5-7."""

    model_config = ConfigDict(extra="forbid")

    environment: str = "development"
    log_level: str = "INFO"
    json_logs: bool = True
    project_root: Path
    storage: StorageSettings = Field(default_factory=StorageSettings)
    media: MediaSettings = Field(default_factory=MediaSettings)
    provider: ProviderSettings = Field(default_factory=ProviderSettings)
    generation: GenerationSettings = Field(default_factory=GenerationSettings)
    validation: ValidationPolicy = Field(default_factory=ValidationPolicy)
    assembly: AssemblyOptions = Field(default_factory=AssemblyOptions)

    @field_validator("environment")
    @classmethod
    def _valid_environment(cls, value: str) -> str:
        if value not in {"development", "test", "production"}:
            raise ValueError(
                "environment must be development, test, or production"
            )
        return value

    @field_validator("log_level")
    @classmethod
    def _valid_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in _SUPPORTED_LOG_LEVELS:
            raise ValueError(
                "log_level must be one of: " + ", ".join(sorted(_SUPPORTED_LOG_LEVELS))
            )
        return normalized

    @property
    def storage_root(self) -> Path:
        return Path(self.storage.root)

    @property
    def registry_path(self) -> Path:
        if self.storage.registry_path is not None:
            return Path(self.storage.registry_path)
        return self.storage_root / "artifact_registry.json"

    @property
    def temp_root(self) -> Path:
        if self.storage.temp_root is not None:
            return Path(self.storage.temp_root)
        return self.storage_root / ".work"

    def public_dict(self) -> dict[str, Any]:
        """Return settings safe for logs/CLI output (credentials excluded)."""

        return self.model_dump(mode="json")


def _default_project_root() -> Path:
    # .../<repo>/src/ai_video_generator/core/settings.py
    return Path(__file__).resolve().parents[3]


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigurationError(
            f"Configuration file does not exist: {path}",
            details={"path": str(path)},
        )
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(
            f"Configuration file could not be read: {path}",
            details={"path": str(path)},
            cause=exc,
        ) from exc
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ConfigurationError(
            f"Configuration file must contain a mapping: {path}",
            details={"path": str(path)},
        )
    return payload


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _parse_bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ConfigurationError(
        f"{name} must be a boolean value",
        details={"variable": name, "value": value},
    )


def _parse_int(value: str, *, name: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigurationError(
            f"{name} must be an integer",
            details={"variable": name, "value": value},
            cause=exc,
        ) from exc


def _parse_float(value: str, *, name: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigurationError(
            f"{name} must be a number",
            details={"variable": name, "value": value},
            cause=exc,
        ) from exc


def _set_nested(data: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    cursor = data
    for key in path[:-1]:
        child = cursor.get(key)
        if not isinstance(child, dict):
            child = {}
            cursor[key] = child
        cursor = child
    cursor[path[-1]] = value


def _apply_env_overrides(
    data: dict[str, Any], env: Mapping[str, str]
) -> dict[str, Any]:
    overrides: list[tuple[str, tuple[str, ...], str]] = [
        ("AIVG_ENVIRONMENT", ("environment",), "str"),
        ("AIVG_LOG_LEVEL", ("log_level",), "str"),
        ("AIVG_JSON_LOGS", ("json_logs",), "bool"),
        ("AIVG_STORAGE_ROOT", ("storage", "root"), "str"),
        ("AIVG_STORAGE_TEMP_ROOT", ("storage", "temp_root"), "str"),
        ("AIVG_STORAGE_REGISTRY_PATH", ("storage", "registry_path"), "str"),
        ("AIVG_MEDIA_FFMPEG_PATH", ("media", "ffmpeg_path"), "str"),
        ("AIVG_MEDIA_FFPROBE_PATH", ("media", "ffprobe_path"), "str"),
        ("AIVG_MEDIA_PROBE_TIMEOUT_SECONDS", ("media", "probe_timeout_seconds"), "float"),
        ("AIVG_MEDIA_RENDER_TIMEOUT_SECONDS", ("media", "render_timeout_seconds"), "float"),
        ("AIVG_PROVIDER", ("provider", "id"), "str"),
        ("AIVG_GEMINI_MODEL", ("provider", "gemini", "model"), "str"),
        ("AIVG_GEMINI_ASPECT_RATIO", ("provider", "gemini", "aspect_ratio"), "str"),
        ("AIVG_GEMINI_RESOLUTION", ("provider", "gemini", "resolution"), "str"),
        ("AIVG_WHITEBOARD_WIDTH", ("provider", "whiteboard", "width"), "int"),
        ("AIVG_WHITEBOARD_HEIGHT", ("provider", "whiteboard", "height"), "int"),
        ("AIVG_WHITEBOARD_FPS", ("provider", "whiteboard", "fps"), "int"),
        ("AIVG_WHITEBOARD_PRESET", ("provider", "whiteboard", "preset"), "str"),
        ("AIVG_WHITEBOARD_CRF", ("provider", "whiteboard", "crf"), "int"),
        ("AIVG_TTS_NARRATOR", ("provider", "whiteboard", "narrator"), "str"),
        ("AIVG_TTS_RATE", ("provider", "whiteboard", "tts_rate"), "int"),
        ("AIVG_GENERATION_MAX_ATTEMPTS", ("generation", "max_attempts"), "int"),
        (
            "AIVG_GENERATION_BACKOFF_BASE_SECONDS",
            ("generation", "backoff_base_seconds"),
            "float",
        ),
        (
            "AIVG_GENERATION_BACKOFF_MAX_SECONDS",
            ("generation", "backoff_max_seconds"),
            "float",
        ),
        (
            "AIVG_GENERATION_BACKOFF_JITTER_SECONDS",
            ("generation", "backoff_jitter_seconds"),
            "float",
        ),
        ("AIVG_GENERATION_TIMEOUT_SECONDS", ("generation", "timeout_seconds"), "float"),
        (
            "AIVG_GENERATION_MAX_VALIDATION_ATTEMPTS",
            ("generation", "max_validation_attempts"),
            "int",
        ),
        (
            "AIVG_VALIDATION_MIN_SCENE_DURATION_SECONDS",
            ("validation", "min_scene_duration_seconds"),
            "float",
        ),
        (
            "AIVG_VALIDATION_MAX_SCENE_DURATION_SECONDS",
            ("validation", "max_scene_duration_seconds"),
            "float",
        ),
        ("AIVG_VALIDATION_REQUIRE_AUDIO", ("validation", "require_audio"), "bool"),
        ("AIVG_VALIDATION_MIN_WIDTH", ("validation", "min_width"), "int"),
        ("AIVG_VALIDATION_MIN_HEIGHT", ("validation", "min_height"), "int"),
        ("AIVG_VALIDATION_MIN_FPS", ("validation", "min_fps"), "float"),
        ("AIVG_ASSEMBLY_INCLUDE_INTRO", ("assembly", "include_intro"), "bool"),
        ("AIVG_ASSEMBLY_INCLUDE_OUTRO", ("assembly", "include_outro"), "bool"),
        ("AIVG_ASSEMBLY_WIDTH", ("assembly", "width"), "int"),
        ("AIVG_ASSEMBLY_HEIGHT", ("assembly", "height"), "int"),
        ("AIVG_ASSEMBLY_FPS", ("assembly", "fps"), "int"),
        ("AIVG_ASSEMBLY_TRANSITION", ("assembly", "transition"), "str"),
        (
            "AIVG_ASSEMBLY_TRANSITION_DURATION_SECONDS",
            ("assembly", "transition_duration_seconds"),
            "float",
        ),
        (
            "AIVG_ASSEMBLY_SHOW_SCENE_TITLES",
            ("assembly", "show_scene_titles"),
            "bool",
        ),
        ("AIVG_ASSEMBLY_SHOW_PROGRESS", ("assembly", "show_progress"), "bool"),
        ("AIVG_ASSEMBLY_CRF", ("assembly", "crf"), "int"),
        ("AIVG_ASSEMBLY_PRESET", ("assembly", "preset"), "str"),
    ]

    merged = _deep_merge({}, data)
    for variable, path, kind in overrides:
        raw = env.get(variable)
        if raw is None:
            continue
        if kind == "str":
            if raw == "" and path[-1] in {"temp_root", "registry_path"}:
                value: Any = None
            else:
                value = raw
        elif kind == "bool":
            value = _parse_bool(raw, name=variable)
        elif kind == "int":
            value = _parse_int(raw, name=variable)
        elif kind == "float":
            value = _parse_float(raw, name=variable)
        else:  # pragma: no cover - defensive
            raise ConfigurationError(f"Unsupported environment override type: {kind}")
        _set_nested(merged, path, value)

    api_key = env.get("GEMINI_API_KEY")
    if api_key:
        _set_nested(merged, ("provider", "gemini_api_key"), SecretStr(api_key))
    return merged


def _resolve_path(value: Path, project_root: Path) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = project_root / candidate
    return candidate.resolve()


def _resolve_paths(settings: AppSettings) -> AppSettings:
    root = _resolve_path(settings.storage.root, settings.project_root)
    temp_root = (
        _resolve_path(settings.storage.temp_root, settings.project_root)
        if settings.storage.temp_root is not None
        else root / ".work"
    )
    registry_path = (
        _resolve_path(settings.storage.registry_path, settings.project_root)
        if settings.storage.registry_path is not None
        else root / "artifact_registry.json"
    )
    return settings.model_copy(
        update={
            "storage": settings.storage.model_copy(
                update={
                    "root": root,
                    "temp_root": temp_root,
                    "registry_path": registry_path,
                }
            ),
            "project_root": _resolve_path(settings.project_root, Path.cwd()),
        }
    )


def load_settings(
    config_dir: Path | str | None = None,
    *,
    project_root: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    load_dotenv_file: bool = True,
) -> AppSettings:
    """Load and validate settings.

    ``env`` replaces the process environment when supplied (useful for tests).
    ``load_dotenv_file`` is ignored when an explicit mapping is supplied.
    """

    root = (
        Path(project_root).expanduser().resolve()
        if project_root is not None
        else _default_project_root()
    )
    config_root = (
        Path(config_dir).expanduser()
        if config_dir is not None
        else root / "config"
    )
    if not config_root.is_absolute():
        config_root = root / config_root
    config_root = config_root.resolve()

    environment: Mapping[str, str]
    if env is not None:
        environment = env
    else:
        if load_dotenv_file:
            dotenv_path = root / ".env"
            if dotenv_path.is_file():
                try:
                    from dotenv import load_dotenv

                    load_dotenv(dotenv_path, override=False)
                except ImportError:  # pragma: no cover - optional dependency
                    pass
        environment = os.environ

    settings_path = config_root / "settings.yaml"
    payload = _read_yaml(settings_path)
    video_path = config_root / "video_generation.yaml"
    if video_path.is_file():
        payload = _deep_merge(payload, _read_yaml(video_path))
    elif config_dir is not None:
        # A custom configuration directory must be complete enough to be explicit.
        raise ConfigurationError(
            f"Configuration file does not exist: {video_path}",
            details={"path": str(video_path)},
        )

    payload = _apply_env_overrides(payload, environment)
    payload.setdefault("project_root", root)
    try:
        settings = AppSettings.model_validate(payload)
    except ValidationError as exc:
        raise ConfigurationError(
            "Application settings are invalid",
            details={"errors": exc.errors(include_url=False)},
            cause=exc,
        ) from exc
    return _resolve_paths(settings)


__all__ = [
    "AppSettings",
    "GeminiSettings",
    "GenerationSettings",
    "MediaSettings",
    "ProviderSettings",
    "StorageSettings",
    "WhiteboardSettings",
    "load_settings",
]
