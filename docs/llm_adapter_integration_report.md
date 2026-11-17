# LLM Adapter Integration Report

**Date:** 2026-11-17
**Branch:** `integration/full-team-integration`
**Base verified HEAD:** `8184e1e4637a5725683f873a641670908de57518`
**Adapter commit:** `1cb43c72e27867c13ce270190960b3e4c7a01d8a`
**Scope:** first production structured-output LLM integration for the planning
agents.

## 1. Provider selected and reason

**Provider:** Google Gemini through the optional `google-genai` SDK.
**Default model:** `gemini-3.8-flash`, configurable with `AIVG_LLM_MODEL` or
`config/settings.yaml`.

Reasons:

- The repository already declares an optional `google-genai` dependency and
  `GEMINI_API_KEY` for the video provider, so Gemini adds no new vendor or
  credential family.
- Gemini supports JSON Schema structured output through
  `response_json_schema`, which matches the existing `LLMRequest.response_schema`
  field without changing planning contracts.
- The `LLMClient` protocol is synchronous and one-call-per-agent; the standard
  `client.models.generate_content` API fits without an async rewrite.
- One provider is implemented. No fallback provider, auto-routing, or silent
  vendor selection was introduced.

The SDK is imported lazily. Unit tests and artifact-only runs do not need
`google-genai` installed.

## 2. Architecture

```text
ScriptAgent / WhiteboardDirectorAgent / VisualDesignAgent
        |  LLMRequest (prompts + JSON Schema + temperature + metadata)
        v
LLMClient protocol                     (unchanged)
        v
GeminiLLMClient                        integrations/gemini_llm_client.py
   - sanitize schema to Gemini-supported subset
   - request application/json + response_json_schema
   - validate returned JSON object with jsonschema
   - bounded retries for transient failures only
   - typed planning errors; no prompt/key logging
        v
google-genai SDK -> Gemini API
```

Construction:

```text
AppSettings.llm + ProviderSettings.gemini_api_key
        -> provider_factory.build_llm_client()
        -> pipeline_factory.build_planning_pipeline()
        -> three agents share one client
```

`build_planning_pipeline(settings, llm_client=None)` builds the production
client when no client is injected. Passing a fake client or a pre-generated
artifact directory is unchanged.

## 3. Files changed

**New**

- `src/ai_video_generator/integrations/gemini_llm_client.py` - adapter, schema
  filtering, retries, error mapping, structured-output validation.
- `scripts/llm_smoke_test.py` - optional one-request live smoke command.
- `tests/unit/test_gemini_llm_client.py` - 14 deterministic adapter/agent tests.
- `tests/unit/test_config_integration.py` gains 5 configuration/factory tests
  (19 new tests total over the 297-test baseline).
- `docs/llm_adapter_integration_report.md` - this report.

**Modified**

- `src/ai_video_generator/integrations/llm_provider.py` - re-exports the stable
  boundary and the Gemini adapter.
- `src/ai_video_generator/integrations/provider_factory.py` - `build_llm_client`.
- `src/ai_video_generator/services/pipeline_factory.py` - optional injected client;
  configuration temperature applied to all three agents.
- `src/ai_video_generator/core/settings.py` - `LLMSettings`, `llm.*`, and
  `AIVG_LLM_*` overrides.
- `config/settings.yaml`, `.env.example`, `config/agents.yaml`, `pyproject.toml`.
- `scripts/run_lesson_pipeline.py` - live planning mode when `--planning-dir` is
  omitted; `--save-planning-dir`; existing artifact mode retained.
- `scripts/README.md`, `README.md`, and architecture/workflow/pipeline/overview/
  roadmap docs.
- `tests/unit/test_config_integration.py` - config, factory, and credential tests.

## 4. Contract compatibility

| Contract | Change | Status |
| --- | --- | --- |
| `LLMClient.generate(LLMRequest) -> LLMResponse` | None | Unchanged |
| `LLMRequest` fields and validation | None | Unchanged |
| `LLMResponse` fields and validation | None | Unchanged |
| Script/Whiteboard/Visual agent public signatures | None | Unchanged |
| Fake LLM clients in tests | None | Still work |
| `build_planning_pipeline(settings, client)` | Second argument becomes optional | Backward compatible |
| Planning dataclasses | None | Unchanged |
| Provider SDK in agent code | Never imported | Isolated |
| JSON Schema sent to Gemini | Sanitized subset | Full schema still validated locally |

Structured-output validation happens twice by design:

1. The adapter parses one JSON object and validates it against the sanitized
   JSON Schema with `jsonschema` (Draft 2020-12).
2. The agents run their existing strict contract and semantic validators, which
   enforce cross-field rules JSON Schema cannot express (segment/key-point
   references, geometry, continuity, action-type payloads).

The adapter does **not** repair invalid output or convert it into a partial
success.

## 5. Retry and error policy

- Retryable: timeouts, connection failures, HTTP 408/409/425/429, HTTP 5xx, and
  statuses `ABORTED`, `DEADLINE_EXCEEDED`, `INTERNAL`, `RESOURCE_EXHAUSTED`,
  `UNAVAILABLE`.
- Non-retryable: 400/`INVALID_ARGUMENT`, 401/403/`UNAUTHENTICATED`/
  `PERMISSION_DENIED`, malformed JSON, schema mismatch, empty/blocked response,
  missing credentials, missing SDK.
- Bounded exponential backoff with jitter, capped by `llm.backoff_max_seconds`;
  total attempts capped by `llm.max_attempts`.
- The SDK's own retry loop is explicitly disabled
  (`HttpRetryOptions(attempts=1)`), so the configured budget is exact.
- Authentication errors state that `GEMINI_API_KEY` must be checked. The key and
  the request prompt are removed from surfaced errors and never logged.

## 6. Configuration

Required for live planning:

```bash
export GEMINI_API_KEY=...
python -m pip install -e ".[google]"   # google-genai>=2,<3
```

Optional `AIVG_*` overrides:

| Variable | Default |
| --- | --- |
| `AIVG_LLM_PROVIDER` | `gemini` (only supported value) |
| `AIVG_LLM_MODEL` | `gemini-3.8-flash` |
| `AIVG_LLM_TEMPERATURE` | `0.2` |
| `AIVG_LLM_MAX_ATTEMPTS` | `3` |
| `AIVG_LLM_BACKOFF_BASE_SECONDS` | `1.0` |
| `AIVG_LLM_BACKOFF_MAX_SECONDS` | `8.0` |
| `AIVG_LLM_BACKOFF_JITTER_SECONDS` | `0.25` |
| `AIVG_LLM_TIMEOUT_SECONDS` | `120` |
| `AIVG_LLM_MAX_OUTPUT_TOKENS` | `8192` |

`GEMINI_API_KEY` remains the single credential variable and is stored as a
Pydantic `SecretStr`.

## 7. Tests and exact results

Verified in this environment (Python 3.14.4, pydantic 2.14.3, jsonschema 4.x,
google-genai 2.34.0 installed but no credentials):

```text
$ python -m pytest -q
316 passed in 12.21s
```

New deterministic coverage (fake SDK at the network boundary, no paid calls):

- successful structured response and response metadata;
- structured config (JSON MIME type, sanitized schema, temperature, system
  instruction);
- invalid JSON rejected without retry;
- schema mismatch and invalid field types rejected without retry;
- empty/blocked response rejected;
- timeout retried once then succeeds;
- rate limit retried to the bounded limit;
- authentication failure not retried, key and prompt absent from errors;
- invalid request not retried;
- missing API key fails at construction with `ConfigurationError`;
- missing `google-genai` fails with a clear configuration error;
- schema sanitizer preserves structure and drops unsupported keywords;
- all three real agent schemas sanitize to valid Draft 2020-12 schemas;
- Script and Whiteboard agents run through the production adapter;
- factory and environment configuration, including injected transport,
  unsupported provider rejection, missing credentials, and fail-fast missing SDK;
  fast-failure occurs before any planning call.

Existing 297 tests still pass. Compilation and import checks are listed in
section 9.

## 8. Live smoke-test status

**Not executed.** No `GEMINI_API_KEY` is configured in this environment.

The command exists:

```bash
python scripts/llm_smoke_test.py --json
```

It makes one minimal request, validates `{"status":"ok","message":"..."}`, and
prints provider/model/latency, or a concise error. It does not plan a lesson or
render video and is not part of pytest. The missing-credential path was verified
locally: exit code `1` with the message "GEMINI_API_KEY is not configured...".

**No claim is made that live Gemini generation works.** A successful live request
must be recorded manually after running the command with a real key.

## 9. Verification commands

```text
python -m pytest -q                               316 passed in 12.21s
python -m compileall -q src tests examples scripts  passed
import walk of all package modules                 passed
git diff --check                                   passed
python scripts/llm_smoke_test.py --json            exit 1, missing credentials (expected)
```

## 10. Remaining limitations

- Live Gemini structured-output behavior, model availability, and schema
  acceptance are unverified.
- The default model may be renamed or retired; update `llm.model`.
- The adapter sends a conservative JSON Schema subset. Unsupported constraints
  (`pattern`, `not`, `allOf`, `additionalProperties`, length limits) are dropped
  and enforced only by agent contract validation.
- No token accounting or cost budget. A full lesson makes three calls per scene,
  so cost grows with scene count.
- Retries are per call. Planning is not resumable across scenes or runs.
- No prompt/response persistence or audit log; logs contain metadata only.
- If a response is truncated at `max_output_tokens`, the adapter raises a format
  error rather than requesting a continuation.
- One provider only. Another provider requires a new `LLMClient` implementation
  and a factory branch, with no agent changes.
- The smoke test is paid and must be run deliberately.
