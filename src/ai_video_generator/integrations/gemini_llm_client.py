"""Google Gemini structured-output adapter for the planning agents.

Responsibility: implement the provider-agnostic ``LLMClient`` protocol with one
production provider, keeping SDK usage, credentials, retries, and error mapping
out of agent logic.
Workflow: constructed from ``core.settings.LLMSettings`` by
``integrations.provider_factory`` and injected into the Script, Whiteboard
Director, and Example & Visual agents.
Inputs: an ``LLMRequest`` with system/user prompts and a JSON Schema, plus
configuration for model, temperature, timeout, output budget, and retry policy.
Outputs: an ``LLMResponse`` containing a JSON object validated against the
request schema.
Interacts with: ``agents.scene_planning.llm``, typed planning errors, core
settings/exceptions/logging, and the optional ``google-genai`` SDK.

Provider facts:

- Authentication uses ``GEMINI_API_KEY`` only; the key is never logged or
  included in errors.
- Structured output is requested with JSON MIME type and a sanitized JSON Schema
  containing only the subset Gemini documents as supported. The project's strict
  contract validators remain authoritative for cross-field rules that JSON Schema
  cannot express.
- Retries are bounded and only apply to timeouts, connection failures, rate
  limits, and 5xx/transient statuses. Authentication and invalid-request errors
  are never retried. The SDK's own retry loop is explicitly disabled so this
  adapter's budget is exact.
- Tests inject a fake SDK client, so no network call is required by the suite.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

import jsonschema
from pydantic import SecretStr

from ..agents.scene_planning.errors import LLMProviderError, LLMResponseFormatError
from ..agents.scene_planning.llm import LLMRequest, LLMResponse, parse_json_object
from ..core.exceptions import ConfigurationError
from ..core.logging import get_logger

logger = get_logger(__name__)

PROVIDER_ID = "gemini"
DEFAULT_MODEL = "gemini-3.8-flash"

# Gemini documents only a subset of JSON Schema. The full internal schemas also
# contain patterns, conditionals, and additionalProperties, which are enforced by
# the agent contract validators instead of being sent to the provider.
_ALLOWED_SCHEMA_KEYS = frozenset(
    {
        "anyOf",
        "description",
        "enum",
        "format",
        "items",
        "maxItems",
        "maximum",
        "minItems",
        "minimum",
        "nullable",
        "properties",
        "required",
        "title",
        "type",
    }
)

_RETRYABLE_STATUS_CODES = frozenset({408, 409, 425, 429})
_RETRYABLE_STATUS_NAMES = frozenset(
    {
        "ABORTED",
        "DEADLINE_EXCEEDED",
        "INTERNAL",
        "RESOURCE_EXHAUSTED",
        "UNAVAILABLE",
    }
)
_AUTH_STATUS_CODES = frozenset({401, 403})
_AUTH_STATUS_NAMES = frozenset({"PERMISSION_DENIED", "UNAUTHENTICATED"})


def _sanitize_schema(value: Any) -> Any:
    """Return the Gemini-compatible subset of a JSON Schema value."""
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key not in _ALLOWED_SCHEMA_KEYS:
                continue
            if key == "properties" and isinstance(item, Mapping):
                result[key] = {
                    name: _sanitize_schema(subschema)
                    for name, subschema in item.items()
                }
            elif key in {"items", "anyOf"}:
                if isinstance(item, list):
                    result[key] = [_sanitize_schema(entry) for entry in item]
                else:
                    result[key] = _sanitize_schema(item)
            else:
                result[key] = item
        return result
    if isinstance(value, list):
        return [_sanitize_schema(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class _Failure:
    retryable: bool
    status_code: int | None
    status: str | None
    message: str


def _failure_for(exc: BaseException, *, api_key: str | None) -> _Failure:
    raw_code = getattr(exc, "code", None)
    code = raw_code if isinstance(raw_code, int) else None
    raw_status = getattr(exc, "status", None)
    status = str(raw_status).upper() if raw_status else None
    provider_message = str(getattr(exc, "message", "") or exc)
    if api_key:
        provider_message = provider_message.replace(api_key, "<redacted>")
    provider_message = provider_message.splitlines()[0][:300]
    error_type = type(exc).__name__

    if code in _AUTH_STATUS_CODES or status in _AUTH_STATUS_NAMES:
        return _Failure(
            retryable=False,
            status_code=code,
            status=status,
            message=(
                "Gemini authentication failed. Verify GEMINI_API_KEY and API "
                "access for the configured model."
            ),
        )
    if code == 429 or status == "RESOURCE_EXHAUSTED":
        return _Failure(
            retryable=True,
            status_code=code,
            status=status,
            message="Gemini rate limit or quota exceeded.",
        )
    if code in _RETRYABLE_STATUS_CODES or (code is not None and code >= 500):
        return _Failure(
            retryable=True,
            status_code=code,
            status=status,
            message="Gemini service is temporarily unavailable.",
        )
    if status in _RETRYABLE_STATUS_NAMES:
        return _Failure(
            retryable=True,
            status_code=code,
            status=status,
            message="Gemini service is temporarily unavailable.",
        )
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return _Failure(
            retryable=True,
            status_code=code,
            status=status,
            message="Gemini request timed out or the connection failed.",
        )
    if code == 400 or status == "INVALID_ARGUMENT":
        return _Failure(
            retryable=False,
            status_code=code,
            status=status,
            message=(
                "Gemini rejected the structured-output request "
                "(HTTP 400/INVALID_ARGUMENT). The response schema or prompt may "
                "use an unsupported shape; no automatic retry was attempted."
            ),
        )
    lowered = error_type.lower()
    retryable = "timeout" in lowered or "connect" in lowered
    return _Failure(
        retryable=retryable,
        status_code=code,
        status=status,
        message=(
            f"Gemini request failed ({error_type})."
            + (f" Provider message: {provider_message}" if provider_message else "")
        ),
    )


def _extract_text(response: Any) -> str:
    try:
        text = response.text
    except Exception as exc:  # SDK raises for blocked/empty candidates
        raise LLMResponseFormatError(
            "Gemini returned no usable text; the response may have been blocked "
            "or truncated"
        ) from exc
    if not isinstance(text, str) or not text.strip():
        raise LLMResponseFormatError(
            "Gemini returned an empty text response"
        )
    return text


def _schema_path(parts: Any) -> str:
    path = "$"
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}"
    return path


class GeminiLLMClient:
    """Structured-output ``LLMClient`` backed by the Gemini API.

    ``client`` and ``client_factory`` exist for deterministic tests and alternate
    SDK transports. Production callers should pass only ``api_key`` and
    configuration; the SDK client is created lazily on first use.
    """

    provider = PROVIDER_ID

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str = DEFAULT_MODEL,
        max_attempts: int = 3,
        timeout_seconds: float = 120.0,
        max_output_tokens: int = 8192,
        backoff_base_seconds: float = 1.0,
        backoff_max_seconds: float = 8.0,
        backoff_jitter_seconds: float = 0.25,
        client: Any | None = None,
        client_factory: Callable[[str, float], Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        random_source: random.Random | None = None,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ConfigurationError("llm.model must be a non-empty string")
        if max_attempts < 1:
            raise ConfigurationError("llm.max_attempts must be at least 1")
        if timeout_seconds <= 0:
            raise ConfigurationError("llm.timeout_seconds must be positive")
        if max_output_tokens < 1:
            raise ConfigurationError("llm.max_output_tokens must be positive")
        if min(
            backoff_base_seconds,
            backoff_max_seconds,
            backoff_jitter_seconds,
        ) < 0:
            raise ConfigurationError("LLM backoff values must be non-negative")
        if backoff_max_seconds < backoff_base_seconds:
            raise ConfigurationError(
                "llm.backoff_max_seconds must be >= llm.backoff_base_seconds"
            )
        if client is None and client_factory is None and not api_key:
            raise ConfigurationError(
                "GEMINI_API_KEY is not configured. Set it in the environment or "
                "local .env file to use the Gemini planning adapter.",
                details={"variable": "GEMINI_API_KEY"},
            )

        self.model = model.strip()
        self.max_attempts = max_attempts
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_max_seconds = backoff_max_seconds
        self.backoff_jitter_seconds = backoff_jitter_seconds
        self._api_key = SecretStr(api_key) if api_key else None
        self._client = client
        self._client_factory = client_factory
        self._sleep = sleep
        self._random = random_source or random.Random()

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(provider={self.provider!r}, "
            f"model={self.model!r}, max_attempts={self.max_attempts})"
        )

    def check_ready(self) -> None:
        """Construct the SDK client if needed without making a model call."""
        self._ensure_client()

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        if self._api_key is None:
            raise ConfigurationError(
                "GEMINI_API_KEY is not configured for the Gemini planning adapter",
                details={"variable": "GEMINI_API_KEY"},
            )
        api_key = self._api_key.get_secret_value()
        if self._client_factory is not None:
            self._client = self._client_factory(api_key, self.timeout_seconds)
            return self._client
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise ConfigurationError(
                "google-genai is not installed. Install the optional 'google' "
                "extra to use the Gemini planning adapter.",
                details={"package": "google-genai"},
                cause=exc,
            ) from exc
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=int(self.timeout_seconds * 1000),
                # Retries are owned by this adapter; disable the SDK's default
                # retry loop so the configured budget is exact.
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
        return self._client

    def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate one JSON object and validate it against the request schema."""
        if not isinstance(request, LLMRequest):
            raise TypeError("request must be an LLMRequest")

        client = self._ensure_client()
        schema = _sanitize_schema(dict(request.response_schema))
        try:
            jsonschema.Draft202012Validator.check_schema(schema)
        except jsonschema.SchemaError as exc:
            raise ConfigurationError(
                "The request response schema is not valid JSON Schema after "
                "Gemini compatibility filtering",
                details={"schema": request.response_schema_name},
                cause=exc,
            ) from exc

        config: dict[str, Any] = {
            "system_instruction": request.system_prompt,
            "response_mime_type": "application/json",
            "response_json_schema": schema,
            "temperature": request.temperature,
            "max_output_tokens": self.max_output_tokens,
        }
        agent = request.metadata.get("agent", "unknown")
        started = time.monotonic()

        for attempt in range(1, self.max_attempts + 1):
            try:
                response = client.models.generate_content(
                    model=self.model,
                    contents=request.user_prompt,
                    config=config,
                )
                text = _extract_text(response)
                payload = parse_json_object(text)
                self._validate_payload(
                    payload,
                    schema,
                    request.response_schema_name,
                )
                elapsed_ms = int((time.monotonic() - started) * 1000)
                logger.info(
                    "LLM structured response received",
                    extra={
                        "provider": self.provider,
                        "model": self.model,
                        "agent": agent,
                        "attempt": attempt,
                        "duration_ms": elapsed_ms,
                        "schema": request.response_schema_name,
                    },
                )
                model_name = (
                    getattr(response, "model_version", None)
                    or getattr(response, "model", None)
                    or self.model
                )
                return LLMResponse(
                    content=text,
                    model=str(model_name),
                    provider=self.provider,
                )
            except LLMResponseFormatError:
                raise
            except ConfigurationError:
                raise
            except Exception as exc:
                failure = _failure_for(
                    exc,
                    api_key=(
                        self._api_key.get_secret_value()
                        if self._api_key is not None
                        else None
                    ),
                )
                if not failure.retryable or attempt >= self.max_attempts:
                    logger.error(
                        "LLM request failed",
                        extra={
                            "provider": self.provider,
                            "model": self.model,
                            "agent": agent,
                            "attempt": attempt,
                            "max_attempts": self.max_attempts,
                            "retryable": failure.retryable,
                            "status_code": failure.status_code,
                            "status": failure.status,
                            "error_type": type(exc).__name__,
                        },
                    )
                    raise LLMProviderError(
                        failure.message,
                        details=(
                            f"provider={self.provider}",
                            f"model={self.model}",
                            f"attempts={attempt}",
                            f"error_type={type(exc).__name__}",
                            *(
                                ()
                                if failure.status_code is None
                                else (f"status_code={failure.status_code}",)
                            ),
                            *(
                                ()
                                if failure.status is None
                                else (f"status={failure.status}",)
                            ),
                        ),
                    ) from exc
                delay = self._backoff_delay(attempt)
                logger.warning(
                    "LLM request failed; retrying",
                    extra={
                        "provider": self.provider,
                        "model": self.model,
                        "agent": agent,
                        "attempt": attempt,
                        "max_attempts": self.max_attempts,
                        "status_code": failure.status_code,
                        "status": failure.status,
                        "retry_in_seconds": round(delay, 3),
                    },
                )
                self._sleep(delay)

        raise LLMProviderError(  # pragma: no cover - loop always returns/raises
            "Gemini request exhausted its retry budget",
            details=(f"provider={self.provider}", f"model={self.model}"),
        )

    def _validate_payload(
        self,
        payload: Mapping[str, Any],
        schema: Mapping[str, Any],
        schema_name: str,
    ) -> None:
        validator = jsonschema.Draft202012Validator(schema)
        error = jsonschema.exceptions.best_match(validator.iter_errors(payload))
        if error is not None:
            raise LLMResponseFormatError(
                "Gemini response does not match the requested JSON Schema "
                f"'{schema_name}' at {_schema_path(error.absolute_path)}: "
                f"{error.message}"
            )

    def _backoff_delay(self, attempt: int) -> float:
        exponential = self.backoff_base_seconds * (2 ** (attempt - 1))
        capped = min(exponential, self.backoff_max_seconds)
        jitter = (
            self._random.uniform(0, self.backoff_jitter_seconds)
            if self.backoff_jitter_seconds > 0
            else 0.0
        )
        return capped + jitter


__all__ = [
    "DEFAULT_MODEL",
    "GeminiLLMClient",
    "PROVIDER_ID",
]
