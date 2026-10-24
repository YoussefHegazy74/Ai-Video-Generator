"""Minimal LLM boundary for the scene-planning agents.

Responsibility: define the structured request/response protocol used by the
Script Agent and Whiteboard Director Agent without selecting a provider.
Workflow: both agents construct an ``LLMRequest``, pass it to an injected
``LLMClient``, and parse the JSON response into validated contracts.
Inputs: system/user prompts, a response schema, optional temperature, and
provider-agnostic metadata.
Outputs: generated text plus optional provider/model metadata.
Interacts with: both agents, prompts, ``errors.py``, and a future adapter around
``integrations.llm_provider``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

from ...models.base import ContractError, ensure_mapping, require_number, require_text
from .errors import LLMResponseFormatError


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """Structured request passed to a provider adapter."""

    system_prompt: str
    user_prompt: str
    response_schema_name: str
    response_schema: Mapping[str, Any]
    temperature: float = 0.2
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_text(self.system_prompt, "llm_request.system_prompt", max_length=50_000)
        require_text(self.user_prompt, "llm_request.user_prompt", max_length=200_000)
        require_text(
            self.response_schema_name,
            "llm_request.response_schema_name",
            max_length=128,
        )
        ensure_mapping(self.response_schema, "llm_request.response_schema")
        if not self.response_schema:
            raise ContractError("llm_request.response_schema must not be empty")
        require_number(
            self.temperature,
            "llm_request.temperature",
            min_value=0.0,
            max_value=2.0,
        )
        metadata = ensure_mapping(self.metadata, "llm_request.metadata")
        for key, value in metadata.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ContractError(
                    "llm_request.metadata keys and values must be strings"
                )


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """Provider response consumed by an agent."""

    content: str
    model: str | None = None
    provider: str | None = None

    def __post_init__(self) -> None:
        require_text(self.content, "llm_response.content", max_length=500_000)
        if self.model is not None:
            require_text(self.model, "llm_response.model", max_length=200)
        if self.provider is not None:
            require_text(self.provider, "llm_response.provider", max_length=200)


@runtime_checkable
class LLMClient(Protocol):
    """Provider-agnostic interface used by scene-planning agents."""

    def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate text for a structured request."""
        ...


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


class _DuplicateKeyError(ValueError):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def _reject_non_finite(value: str) -> Any:
    raise LLMResponseFormatError(
        f"LLM response contains non-finite JSON number '{value}'"
    )


def parse_json_object(content: str) -> dict[str, Any]:
    """Parse a single JSON object, tolerating one complete Markdown code fence.

    Provider adapters should request structured output directly, but a code
    fence is a common wrapping artifact. Incomplete fences, trailing prose,
    duplicate object keys, and non-finite numbers are rejected so malformed
    output cannot be silently accepted or overwritten.
    """
    if not isinstance(content, str):
        raise LLMResponseFormatError("LLM response content must be a string")
    text = content.strip()
    if not text:
        raise LLMResponseFormatError("LLM response content is empty")

    if text.startswith("```"):
        newline = text.find("\n")
        if newline < 0:
            raise LLMResponseFormatError(
                "LLM response contains an incomplete code fence"
            )
        fenced = text[newline + 1 :]
        closing = fenced.rfind("```")
        if closing < 0:
            raise LLMResponseFormatError(
                "LLM response contains an unclosed code fence"
            )
        trailing = fenced[closing + 3 :].strip()
        if trailing:
            raise LLMResponseFormatError(
                "LLM response contains text outside the JSON code fence"
            )
        text = fenced[:closing].strip()
        if not text:
            raise LLMResponseFormatError("LLM response code fence is empty")

    try:
        payload = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_non_finite,
        )
    except _DuplicateKeyError as exc:
        raise LLMResponseFormatError(
            f"LLM response contains duplicate JSON key '{exc.key}'"
        ) from exc
    except LLMResponseFormatError:
        raise
    except json.JSONDecodeError as exc:
        raise LLMResponseFormatError(
            f"LLM response is not valid JSON: {exc.msg} at line {exc.lineno}, "
            f"column {exc.colno}"
        ) from exc

    if not isinstance(payload, dict):
        raise LLMResponseFormatError("LLM response must be a single JSON object")
    return payload


__all__ = [
    "LLMClient",
    "LLMRequest",
    "LLMResponse",
    "parse_json_object",
]
