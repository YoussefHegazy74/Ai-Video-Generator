"""Typed application exception hierarchy.

Every failure that crosses a layer boundary carries a stable machine-readable
code, an actionable message, and optional structured details. Secrets must never
be placed in ``details``.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for expected application failures."""

    code = "app_error"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.cause = cause

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


class ConfigurationError(AppError):
    code = "configuration_error"


class SceneContractError(AppError):
    code = "scene_contract_error"


class SceneStateError(SceneContractError):
    code = "scene_state_error"


class JobStateError(AppError):
    code = "job_state_error"


class StorageError(AppError):
    code = "storage_error"


class StoragePathError(StorageError):
    code = "storage_path_error"


class ArtifactNotFoundError(StorageError):
    code = "artifact_not_found"


class MediaToolUnavailableError(AppError):
    code = "media_tool_unavailable"


class MediaProbeError(AppError):
    code = "media_probe_error"


class TtsUnavailableError(AppError):
    code = "tts_unavailable"


class ProviderError(AppError):
    code = "provider_error"

    def __init__(
        self,
        message: str,
        *,
        provider: str = "unknown",
        retryable: bool = False,
        status_code: int | None = None,
        request_id: str | None = None,
        details: dict[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        merged = dict(details or {})
        merged.setdefault("provider", provider)
        if status_code is not None:
            merged.setdefault("status_code", status_code)
        if request_id:
            merged.setdefault("request_id", request_id)
        super().__init__(message, details=merged, cause=cause)
        self.provider = provider
        self.retryable = retryable
        self.status_code = status_code
        self.request_id = request_id


class ProviderTimeoutError(ProviderError):
    code = "provider_timeout"

    def __init__(self, message: str, **kwargs: Any) -> None:
        kwargs.setdefault("retryable", True)
        super().__init__(message, **kwargs)


class ProviderCapabilityError(ProviderError):
    code = "provider_capability_error"

    def __init__(self, message: str, **kwargs: Any) -> None:
        kwargs.setdefault("retryable", False)
        super().__init__(message, **kwargs)


class ProviderOutputError(ProviderError):
    code = "provider_output_error"


class DuplicateGenerationError(AppError):
    code = "duplicate_generation"

    def __init__(self, message: str, **kwargs: Any) -> None:
        kwargs.setdefault("retryable", True)
        super().__init__(message, **kwargs)


class SceneGenerationError(AppError):
    code = "scene_generation_failed"

    def __init__(
        self,
        message: str,
        *,
        scene_id: str,
        attempts: int,
        retryable: bool,
        failure: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        merged = dict(details or {})
        merged.setdefault("scene_id", scene_id)
        merged.setdefault("attempts", attempts)
        merged.setdefault("retryable", retryable)
        if failure:
            merged.setdefault("failure", failure)
        super().__init__(message, details=merged, cause=cause)
        self.scene_id = scene_id
        self.attempts = attempts
        self.retryable = retryable
        self.failure = failure or {}


class ValidationBlockedError(AppError):
    code = "validation_blocked"

    def __init__(
        self,
        message: str,
        *,
        scene_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged = dict(details or {})
        if scene_id:
            merged.setdefault("scene_id", scene_id)
        super().__init__(message, details=merged, cause=None)
        self.scene_id = scene_id


class AssemblyError(AppError):
    code = "assembly_error"


class AssemblyInputError(AssemblyError):
    code = "assembly_input_error"


class AssemblyRenderError(AssemblyError):
    code = "assembly_render_error"


class AssemblyVerificationError(AssemblyError):
    code = "assembly_verification_error"


class PipelineError(AppError):
    code = "pipeline_error"


class PipelineCancelledError(PipelineError):
    code = "pipeline_cancelled"
