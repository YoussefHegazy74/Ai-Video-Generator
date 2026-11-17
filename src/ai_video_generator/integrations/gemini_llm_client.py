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
- Temperature, top_p, and top_k are deprecated or rejected on Gemini 3.6+ and
  Gemini 4. This adapter sends temperature only to explicitly known legacy model
  families; unknown/newer models are treated conservatively.
- Retries are bounded and only apply to timeouts, connection failures, rate
  limits, and 5xx/transient statuses. Authentication and invalid-request errors
  are never retried. The SDK's own retry loop is explicitly disabled so this
  adapter's budget is exact.
- HTTP 400 diagnostics preserve the provider code, status, message, reason, and
  invalid field, plus model, request parameter names, and schema size. Prompts and
  API keys are never included.
- Tests inject a fake SDK client, so no network call is required by the suite.
"""

from __future__ import annotations

import json
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

# Gemini 3.6+ and Gemini 4 ignore or reject temperature/top_p/top_k. Only send
# temperature for explicitly known legacy families; unknown/newer model names
# are treated conservatively. Temperature remains part of LLMRequest for API
# compatibility and is still honored for legacy models.
_TEMPERATURE_SUPPORTED_PREFIXES = (
    "gemini-1.5-",
    "gemini-2.0-",
    "gemini-2.5-",
)
_SAMPLING_PARAMETER_MARKERS = ("temperature", "top_p", "top_k", "topk")


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
    provider_message: str | None = None
    reason: str | None = None
    field: str | None = None


def _supports_temperature(model: str) -> bool:
    """Return whether temperature is honored by the model.

    Gemini 3.6+ and Gemini 4 models ignore or reject custom sampling parameters.
    Unknown model names are treated conservatively so new Gemini generations do
    not start receiving a parameter they may reject.
    """
    return model.strip().lower().startswith(_TEMPERATURE_SUPPORTED_PREFIXES)


def _safe_provider_text(value: Any, *, api_key: str | None) -> str:
    text = str(value or "")
    if api_key:
        text = text.replace(api_key, "<redacted>")
    return " ".join(text.split())[:500]


def _extract_detail_facts(details: Any) -> tuple[str | None, str | None, str | None]:
    """Extract safe gRPC/HTTP error facts without retaining the raw payload."""
    reason: str | None = None
    field: str | None = None
    message: str | None = None

    def walk(node: Any) -> None:
        nonlocal reason, field, message
        if isinstance(node, Mapping):
            if message is None:
                candidate = node.get("message")
                if isinstance(candidate, str) and candidate.strip():
                    message = candidate
            if reason is None:
                candidate = node.get("reason")
                if isinstance(candidate, str) and candidate.strip():
                    reason = candidate
            for key in ("fieldViolations", "field_violations"):
                violations = node.get(key)
                if isinstance(violations, list):
                    for violation in violations:
                        if not isinstance(violation, Mapping):
                            continue
                        if field is None:
                            field_name = violation.get("field")
                            if isinstance(field_name, str) and field_name.strip():
                                field = field_name
                        if message is None:
                            description = violation.get("description")
                            if (
                                isinstance(description, str)
                                and description.strip()
                            ):
                                message = description
            if field is None:
                field_name = node.get("field")
                if isinstance(field_name, str) and field_name.strip():
                    field = field_name
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(details)
    return reason, field, message


def _failure_for(exc: BaseException, *, api_key: str | None) -> _Failure:
    raw_code = getattr(exc, "code", None)
    code = raw_code if isinstance(raw_code, int) else None
    raw_status = getattr(exc, "status", None)
    status = str(raw_status).upper() if raw_status else None
    raw_details = getattr(exc, "details", None)
    reason, field, detail_message = _extract_detail_facts(raw_details)
    if reason is None:
        reason = status
    provider_message = _safe_provider_text(
        getattr(exc, "message", None) or detail_message or exc,
        api_key=api_key,
    )
    error_type = type(exc).__name__

    def with_provider(message: str) -> str:
        if provider_message:
            message += f" Provider message: {provider_message}"
        if reason:
            message += f" Reason: {reason}."
        if field:
            message += f" Invalid field: {field}."
        return message

    if code in _AUTH_STATUS_CODES or status in _AUTH_STATUS_NAMES:
        return _Failure(
            retryable=False,
            status_code=code,
            status=status,
            message=with_provider(
                "Gemini authentication failed. Verify GEMINI_API_KEY and API "
                "access for the configured model."
            ),
            provider_message=provider_message or None,
            reason=reason,
            field=field,
        )
    if code == 429 or status == "RESOURCE_EXHAUSTED":
        return _Failure(
            retryable=True,
            status_code=code,
            status=status,
            message=with_provider("Gemini rate limit or quota exceeded."),
            provider_message=provider_message or None,
            reason=reason,
            field=field,
        )
    if code in _RETRYABLE_STATUS_CODES or (code is not None and code >= 500):
        return _Failure(
            retryable=True,
            status_code=code,
            status=status,
            message=with_provider(
                "Gemini service is temporarily unavailable."
            ),
            provider_message=provider_message or None,
            reason=reason,
            field=field,
        )
    if status in _RETRYABLE_STATUS_NAMES:
        return _Failure(
            retryable=True,
            status_code=code,
            status=status,
            message=with_provider(
                "Gemini service is temporarily unavailable."
            ),
            provider_message=provider_message or None,
            reason=reason,
            field=field,
        )
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return _Failure(
            retryable=True,
            status_code=code,
            status=status,
            message=with_provider(
                "Gemini request timed out or the connection failed."
            ),
            provider_message=provider_message or None,
            reason=reason,
            field=field,
        )
    if code == 400 or status == "INVALID_ARGUMENT":
        lowered = provider_message.lower()
        if "api key" in lowered or "api_key" in lowered:
            message = (
                "Gemini rejected the API key (HTTP 400/API_KEY_INVALID). Verify "
                "that GEMINI_API_KEY is valid, unrestricted, and belongs to a "
                "project with access to the configured model. No automatic retry "
                "was attempted."
            )
        elif any(marker in lowered for marker in _SAMPLING_PARAMETER_MARKERS):
            message = (
                "Gemini rejected a sampling parameter (HTTP "
                "400/INVALID_ARGUMENT). Gemini 3.6+ and Gemini 4 ignore or "
                "reject temperature, top_p, and top_k; this adapter omits them "
                "for those models. No automatic retry was attempted."
            )
        else:
            message = (
                "Gemini rejected the structured-output request (HTTP "
                "400/INVALID_ARGUMENT). The response schema, request "
                "parameters, or prompt may use an unsupported shape. No "
                "automatic retry was attempted."
            )
        return _Failure(
            retryable=False,
            status_code=code,
            status=status,
            message=with_provider(message),
            provider_message=provider_message or None,
            reason=reason,
            field=field,
        )
    lowered = error_type.lower()
    retryable = "timeout" in lowered or "connect" in lowered
    return _Failure(
        retryable=retryable,
        status_code=code,
        status=status,
        message=with_provider(f"Gemini request failed ({error_type})."),
        provider_message=provider_message or None,
        reason=reason,
        field=field,
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


def describe_request(request: LLMRequest, *, model: str) -> dict[str, Any]:
    """Return safe request diagnostics without prompts, keys, or full schemas.

    This is intentionally independent of credentials so support tooling can show
    the effective provider configuration before attempting a live call.
    """
    if not isinstance(request, LLMRequest):
        raise TypeError("request must be an LLMRequest")
    schema = _sanitize_schema(dict(request.response_schema))
    temperature_included = _supports_temperature(model)
    parameters = [
        "system_instruction",
        "response_mime_type",
        "response_json_schema",
        "max_output_tokens",
    ]
    if temperature_included:
        parameters.append("temperature")
    return {
        "provider": PROVIDER_ID,
        "model": model,
        "response_schema_name": request.response_schema_name,
        "request_parameters": parameters,
        "temperature_included": temperature_included,
        "schema_properties": len(schema.get("properties", {})),
        "schema_chars": len(json.dumps(schema, separators=(",", ":"))),
        "system_prompt_chars": len(request.system_prompt),
        "user_prompt_chars": len(request.user_prompt),
    }


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

    def describe_request(self, request: LLMRequest) -> dict[str, Any]:
        """Return safe, non-sensitive diagnostics for the effective request."""
        return describe_request(request, model=self.model)

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
            "max_output_tokens": self.max_output_tokens,
        }
        sampling_parameters_sent = _supports_temperature(self.model)
        if sampling_parameters_sent:
            config["temperature"] = request.temperature
        else:
            logger.debug(
                "Omitting temperature for a model that does not support "
                "sampling parameters",
                extra={"provider": self.provider, "model": self.model},
            )
        request_parameters = tuple(
            sorted(key for key in config if key != "system_instruction")
        )
        schema_summary = (
            f"properties={len(schema.get('properties', {}))},"
            f"chars={len(json.dumps(schema, separators=(',', ':')))}"
        )
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
                        "sampling_parameters_sent": sampling_parameters_sent,
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
                            "provider_reason": failure.reason,
                            "provider_field": failure.field,
                            "request_parameters": ",".join(request_parameters),
                            "schema": request.response_schema_name,
                            "schema_summary": schema_summary,
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
                            *(
                                ()
                                if failure.reason is None
                                else (f"provider_reason={failure.reason}",)
                            ),
                            *(
                                ()
                                if failure.field is None
                                else (f"provider_field={failure.field}",)
                            ),
                            *(
                                ()
                                if failure.provider_message is None
                                else (
                                    "provider_message="
                                    + failure.provider_message,
                                )
                            ),
                            f"request_parameters={','.join(request_parameters)}",
                            f"schema={request.response_schema_name}",
                            f"schema_summary={schema_summary}",
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
                        "provider_reason": failure.reason,
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
    "describe_request",
]
