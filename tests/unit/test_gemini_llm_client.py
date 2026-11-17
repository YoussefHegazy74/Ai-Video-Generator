"""Tests for the production Gemini structured-output LLM adapter.

Responsibility: verify transport behavior, structured-output validation, retry
policy, credential handling, and compatibility with the existing agents.
Workflow: unit tests use a fake SDK client at the network boundary; no request
leaves the process.
Inputs: deterministic request/response fixtures and synthetic SDK errors.
Outputs: assertions on responses, typed errors, retry counts, and agent contracts.
Interacts with: ``integrations.gemini_llm_client``, the planning agents, and the
existing ``LLMClient`` protocol.
"""

from __future__ import annotations

import copy
import json
import sys
from typing import Any

import pytest

from ai_video_generator.agents.scene_planning.errors import (
    LLMProviderError,
    LLMResponseFormatError,
)
from ai_video_generator.agents.scene_planning.llm import LLMRequest
from ai_video_generator.agents.scripting.script_agent import ScriptAgent
from ai_video_generator.agents.whiteboard.whiteboard_director_agent import (
    WhiteboardDirectorAgent,
)
from ai_video_generator.core.exceptions import ConfigurationError
from ai_video_generator.agents.scene_planning.schemas import (
    SCENE_SCRIPT_OUTPUT_SCHEMA,
    WHITEBOARD_PLAN_OUTPUT_SCHEMA,
)
from ai_video_generator.agents.visual_design.schemas import (
    VISUAL_PLAN_OUTPUT_SCHEMA,
)
from ai_video_generator.integrations.gemini_llm_client import (
    GeminiLLMClient,
    _sanitize_schema,
)

VALID_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "answer": {"type": "string", "pattern": r"\S"},
        "count": {"type": "integer", "minimum": 0},
    },
    "required": ["answer"],
    "allOf": [{"if": {"required": ["count"]}, "then": {"required": ["answer"]}}],
}


class FakeSdkError(Exception):
    """Minimal stand-in for google.genai.errors.APIError."""

    def __init__(
        self,
        code: int,
        message: str = "provider failure",
        status: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message
        self.details = {}


class FakeResponse:
    def __init__(
        self,
        text: str | None,
        *,
        model_version: str = "gemini-3.8-flash",
        text_error: BaseException | None = None,
    ) -> None:
        self._text = text
        self._text_error = text_error
        self.model_version = model_version

    @property
    def text(self) -> str:
        if self._text_error is not None:
            raise self._text_error
        return self._text or ""


class FakeModels:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def generate_content(
        self, *, model: str, contents: str, config: dict[str, Any]
    ) -> Any:
        self.calls.append(
            {"model": model, "contents": contents, "config": copy.deepcopy(config)}
        )
        if not self.outcomes:
            raise AssertionError("fake SDK received more calls than expected")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeSdkClient:
    def __init__(self, *outcomes: Any) -> None:
        self.models = FakeModels(list(outcomes))


def _request() -> LLMRequest:
    return LLMRequest(
        system_prompt="Return a test object.",
        user_prompt="Say ok.",
        response_schema_name="TestObject",
        response_schema=VALID_SCHEMA,
        temperature=0.1,
        metadata={"agent": "test_agent"},
    )


def _client(*outcomes: Any, **kwargs: Any) -> tuple[GeminiLLMClient, FakeSdkClient]:
    sdk = FakeSdkClient(*outcomes)
    return (
        GeminiLLMClient(
            api_key="test-secret-key",
            model="gemini-test",
            client=sdk,
            **kwargs,
        ),
        sdk,
    )


def test_generate_returns_validated_json_and_passes_sanitized_schema():
    client, sdk = _client(FakeResponse('{"answer": "ok", "count": 1}'))

    response = client.generate(_request())

    assert response.content == '{"answer": "ok", "count": 1}'
    assert response.provider == "gemini"
    assert response.model == "gemini-3.8-flash"
    assert len(sdk.models.calls) == 1
    call = sdk.models.calls[0]
    assert call["model"] == "gemini-test"
    assert call["contents"] == "Say ok."
    config = call["config"]
    assert config["system_instruction"] == "Return a test object."
    assert config["response_mime_type"] == "application/json"
    assert config["temperature"] == 0.1
    schema = config["response_json_schema"]
    assert schema["type"] == "object"
    assert "answer" in schema["properties"]
    assert schema["required"] == ["answer"]
    assert "$schema" not in schema
    assert "additionalProperties" not in schema
    assert "allOf" not in schema
    assert "pattern" not in schema["properties"]["answer"]


def test_invalid_json_is_rejected_without_a_retry():
    client, sdk = _client(FakeResponse("not valid json"))

    with pytest.raises(LLMResponseFormatError, match="not valid JSON"):
        client.generate(_request())

    assert len(sdk.models.calls) == 1


def test_schema_validation_failure_is_rejected_without_a_retry():
    client, sdk = _client(FakeResponse('{"count": 2}'))

    with pytest.raises(LLMResponseFormatError, match="does not match.*TestObject"):
        client.generate(_request())

    assert len(sdk.models.calls) == 1


def test_schema_validation_rejects_invalid_field_types():
    client, sdk = _client(FakeResponse('{"answer": 42}'))

    with pytest.raises(LLMResponseFormatError, match="does not match.*TestObject"):
        client.generate(_request())

    assert len(sdk.models.calls) == 1


def test_empty_or_blocked_response_is_rejected():
    client, sdk = _client(FakeResponse(None, text_error=ValueError("blocked")))

    with pytest.raises(LLMResponseFormatError, match="no usable text"):
        client.generate(_request())

    assert len(sdk.models.calls) == 1


def test_timeout_is_retried_then_succeeds():
    sleeps: list[float] = []
    client, sdk = _client(
        TimeoutError("deadline exceeded"),
        FakeResponse('{"answer": "ok"}'),
        sleep=sleeps.append,
        max_attempts=3,
        backoff_base_seconds=0.0,
        backoff_jitter_seconds=0.0,
    )

    response = client.generate(_request())

    assert response.content == '{"answer": "ok"}'
    assert len(sdk.models.calls) == 2
    assert len(sleeps) == 1


def test_rate_limit_is_retried_to_the_bounded_limit():
    sleeps: list[float] = []
    failures = [
        FakeSdkError(429, "quota", "RESOURCE_EXHAUSTED")
        for _ in range(3)
    ]
    client, sdk = _client(
        *failures,
        sleep=sleeps.append,
        max_attempts=3,
        backoff_base_seconds=0.0,
        backoff_jitter_seconds=0.0,
    )

    with pytest.raises(LLMProviderError, match="rate limit"):
        client.generate(_request())

    assert len(sdk.models.calls) == 3
    assert len(sleeps) == 2


def test_authentication_failure_is_not_retried_and_redacts_the_key():
    client, sdk = _client(
        FakeSdkError(401, "bad key test-secret-key", "UNAUTHENTICATED")
    )

    with pytest.raises(LLMProviderError, match="authentication failed") as excinfo:
        client.generate(_request())

    assert len(sdk.models.calls) == 1
    rendered = str(excinfo.value) + repr(excinfo.value.details)
    assert "test-secret-key" not in rendered
    assert "test-secret-key" not in repr(client)
    assert "Say ok." not in rendered


def test_invalid_request_is_not_retried():
    client, sdk = _client(FakeSdkError(400, "bad schema", "INVALID_ARGUMENT"))

    with pytest.raises(LLMProviderError, match="rejected the structured-output"):
        client.generate(_request())

    assert len(sdk.models.calls) == 1


def test_missing_api_key_fails_at_construction():
    with pytest.raises(ConfigurationError, match="GEMINI_API_KEY"):
        GeminiLLMClient(api_key=None)


def test_missing_sdk_dependency_is_a_configuration_error(monkeypatch):
    client = GeminiLLMClient(api_key="test-secret-key")
    monkeypatch.setitem(sys.modules, "google", None)
    monkeypatch.setitem(sys.modules, "google.genai", None)

    with pytest.raises(ConfigurationError, match="google-genai"):
        client.generate(_request())


def test_sanitizer_drops_unsupported_keywords_but_keeps_structure():
    schema = _sanitize_schema(
        {
            "type": "object",
            "title": "T",
            "required": ["a"],
            "properties": {
                "a": {
                    "type": "array",
                    "minLength": 1,
                    "items": {"type": "string", "enum": ["x", "y"]},
                }
            },
            "not": {"required": ["b"]},
        }
    )

    assert schema["title"] == "T"
    assert schema["required"] == ["a"]
    assert schema["properties"]["a"]["items"]["enum"] == ["x", "y"]
    assert "minLength" not in schema["properties"]["a"]
    assert "not" not in schema


def test_script_and_whiteboard_agents_use_the_production_adapter(
    planning_input, script_payload, whiteboard_payload
):
    client, sdk = _client(
        FakeResponse(json.dumps(script_payload)),
        FakeResponse(json.dumps(whiteboard_payload)),
    )

    script = ScriptAgent(client).generate(planning_input)
    plan = WhiteboardDirectorAgent(client).generate_for_scene(
        planning_input, script
    )

    assert script.scene_id == "scene-001"
    assert script.narration.startswith("A neural network is like")
    assert plan.scene_id == "scene-001"
    assert len(plan.drawing.actions) == 4
    assert len(sdk.models.calls) == 2
    assert sdk.models.calls[0]["config"]["response_json_schema"]["properties"]
    assert sdk.models.calls[1]["config"]["response_json_schema"]["properties"]


def test_all_agent_schemas_sanitize_to_valid_gemini_subset():
    import jsonschema

    for schema, expected_required in (
        (SCENE_SCRIPT_OUTPUT_SCHEMA, "scene_id"),
        (WHITEBOARD_PLAN_OUTPUT_SCHEMA, "drawing"),
        (VISUAL_PLAN_OUTPUT_SCHEMA, "items"),
    ):
        sanitized = _sanitize_schema(schema)
        jsonschema.Draft202012Validator.check_schema(sanitized)
        assert sanitized["type"] == "object"
        assert expected_required in sanitized["properties"]
        assert "required" in sanitized
        assert "$schema" not in sanitized
        assert "allOf" not in json.dumps(sanitized)
