# Tests

**Status:** implemented for Parts 5-7. Tests use pytest, synthetic local media,
fake providers, and a fake Gemini client. They make no network calls and no paid
API calls.

## Run

From the repository root:

```powershell
python -m pytest -q
```

`pytest` may not be on `PATH` in this environment; `python -m pytest` is the
supported command. The last local Windows run was **53 passed** in about 42
seconds. FFmpeg/FFprobe must be available for media tests; when they are absent,
those tests skip with an explicit prerequisite message.

## Layout

- `unit/`
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
