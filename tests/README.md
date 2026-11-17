# Tests

**Status:** implemented for the integrated planning + media pipeline. Tests use
pytest, a deterministic fake LLM, synthetic local media, fake providers, and a
fake Gemini client. They make no network calls and no paid API calls.

## Run

From the repository root:

```powershell
python -m pytest -q
```

`pytest` may not be on `PATH` in this environment; `python -m pytest` is the
supported command. The last verified run on Linux (Python 3.14.4, FFmpeg 8.0.1)
was **321 passed** in about 13 seconds. FFmpeg/FFprobe must be available for media
tests; when they are absent, those tests skip with an explicit prerequisite
message.

## Layout

- `unit/`
  - Planning contracts, agents, prompts, schemas, packaging, timing, and
    cross-component flows (`test_contracts.py`, `test_script_agent.py`,
    `test_whiteboard_director_agent.py`, `test_visual_design_agent.py`,
    `test_visual_plan_contract.py`, `test_scene_*`, `test_prompts_and_schemas.py`,
    `test_hardening_regressions.py`).
  - `test_runtime_adapter.py`: planning-to-runtime narration, provenance, text,
    ordering, and ID rejection.
  - `test_lesson_pipeline.py`: agent ordering, previous-plan continuity, runtime
    handoff, and failure propagation.
  - `test_config_integration.py`: shipped config, environment overrides, factory
    wiring, invalid bounds, and missing Gemini credentials.
  - `test_gemini_llm_client.py`: structured-output success, malformed/schema
    failures, retry and no-retry paths, credential redaction, missing SDK/key,
    schema sanitization, and both agents through the production adapter.
  - `test_scene_contract.py`: schema invariants, transitions, artifact-key
    validation, secret redaction, ordering.
  - `test_settings.py`: YAML/env precedence, path resolution, secret exclusion,
    invalid configuration handling.
  - `test_storage.py`: traversal rejection, atomic round trips, lock exclusivity,
    temporary-directory containment, registry provenance/redaction.
  - `test_validation.py`: passing measurable checks, duration/audio/text/style
    failures, unavailable checks, validation-agent rejection persistence.
  - `test_generation_service.py`: measured metadata, retry/backoff, non-retryable
    failure, duplicate lock, reuse, cancellation.
  - `test_gemini_provider.py`: capability honesty, fake-client output handling,
    prompt construction, retryable error mapping, non-HTTPS URI rejection.
- `integration/`
  - `test_lesson_pipeline_integration.py`: real planning-to-video flow with a fake
    LLM and fake provider through FFmpeg generation, validation, and assembly.
  - `test_cli_lesson_pipeline.py`: documented full-pipeline CLI in plan-only mode.
  - `test_assembly.py`: scene-index ordering, normalization, intro/outro cards,
    final verification, assembly gate.
  - `test_orchestrator.py`: complete generate -> validate -> assemble flow,
    retryable rejection -> regeneration, unsatisfiable rejection, cancellation.

## Test media and fakes

`tests/helpers.py` provides:

- `FakeNarrator`: deterministic local WAV writer.
- `FakeProvider`: deterministic 320x180 clip + text manifest, configurable
  failures and silent attempts.
- clip/scene/scene-set factories and `make_generated_scene`,
  `make_validated_scene`.
- `require_ffmpeg`: skips with a prerequisite message when tools are missing.

No real TTS voice, real Gemini credential, or external service is required by the
suite.

## Conventions

- Tests must not contain real credentials.
- Tests must not require paid calls or network access.
- Prefer typed exceptions and observable artifact/state assertions.
- Keep synthetic clips small (320x180, low fps, 1-2 seconds) to bound runtime.
