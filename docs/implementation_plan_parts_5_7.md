# Implementation Plan — Parts 5, 6, and 7

**Status:** implemented and tested (2026-10-25). This document is retained as the
approved plan and implementation record.
**Scope:** scene generation, scene validation, and final video assembly for the
existing AI Educational Video Generator repository.
**Audit date:** 2026-10-24.

## 1. Audit findings

The repository is an intentional skeleton:

- Every Python module under `src/ai_video_generator/` is a docstring-only
  placeholder. There are no classes, functions, dependency manifests, lockfiles,
  API server, database, workers, or tests.
- `config/*.yaml` contain comments only; `.env.example` defines no variables.
- `tests/` contains README placeholders only.
- No Git repository is initialized, so there is no commit history or working
  tree baseline to preserve.
- The runtime environment has Python 3.11.9, Pydantic 2.10.6, PyYAML, Pillow,
  python-dotenv, google-genai 1.65.0, and FFmpeg/FFprobe 8.1.1 on `PATH`.
  `pytest` 9.1.1 was installed as a user development dependency during
  implementation; it is not on `PATH`, so tests run with `python -m pytest`.
  FFmpeg has `libx264`, AAC, `overlay`, `concat`, `fade`, `silencedetect`,
  `blackdetect`, `drawtext`, and the `flite` TTS source.
- Google's official documentation (checked 2026-10-24) states that Veo 3.1
  Preview was shut down on 2026-10-22. The current Gemini video generation API is
  Gemini Omni Flash (`gemini-omni-1.1-flash`), which produces video **with
  audio**, supports 16:9/9:16 and 360p–4K, but exposes no documented duration
  control. It must not be described as producing a requested 10-second clip.

## 2. Architecture decisions

1. **One canonical scene contract.** Pydantic v2 models live in
   `video/scene_contract.py`. `models/scene.py` re-exports them for the existing
   domain-model location; no second schema is created.
2. **Deterministic-first provider strategy.** The default provider is a local
   deterministic whiteboard renderer. It is explicitly not an AI generator; it
   produces accurate text/code/equation frames with Pillow, narration through a
   local TTS adapter, and muxed audio/video through FFmpeg. This is the honest
   fallback for technical text fidelity.
3. **One audiovisual provider contract.** `integrations/audiovisual_generation_provider.py`
   defines capabilities, duration selection, request/result models, and a
   Protocol. Adapters:
   - `integrations/deterministic_whiteboard_provider.py` (default, local);
   - `integrations/gemini_omni_provider.py` (optional real AI adapter; requires
     `GEMINI_API_KEY`, untested live without credentials).
   Providers that only support silent video are rejected rather than silently
   assembled with missing narration.
4. **Storage abstraction.** `integrations/storage_provider.py` defines the
   read/write contract; `storage/file_storage.py` implements safe local keys,
   traversal protection, atomic writes, and job-scoped temporary directories.
   `storage/artifact_registry.py` records provenance. Services use artifact keys,
   never hardcoded absolute paths.
5. **Measured media truth.** FFprobe measurements are authoritative for actual
   duration, streams, codecs, dimensions, and frame rate. Requested durations are
   never reported as actual durations.
6. **Validation is a gate, not a score.** Reports contain pass/warning/fail/
   unavailable checks with observed and expected values. Only blocking failures
   prevent assembly. OCR and semantic synchronization are reported unavailable
   when no implementation exists; they are never faked.
7. **Deterministic assembly.** FFmpeg normalizes every clip to one resolution,
   frame rate, pixel format, audio sample rate, and channel layout, then joins in
   explicit `scene_index` order. Supported transitions are `cut` and a
   non-overlapping `fade_black`; crossfades are deliberately not implemented
   because they trim clip edges and can truncate narration. Intro/outro title
   cards, scene labels, and progress overlays are generated locally.
8. **No invented background infrastructure.** There is no API or worker system
   to integrate with. A synchronous orchestrator with explicit stages, progress
   callbacks, cancellation checks, and persisted job/scene manifests provides the
   integration boundary for a future API or worker.
9. **Configuration.** YAML defaults under `config/` are overlaid by documented
   `AIVG_*` environment variables. Only `GEMINI_API_KEY` is a credential and it
   is read from the environment, never stored or logged.

## 3. Files and responsibilities

### Canonical contracts and core infrastructure

| File | Responsibility |
| --- | --- |
| `src/ai_video_generator/video/scene_contract.py` | Canonical `SceneSpec`, `SceneSet`, `MediaAsset`, generation/retry/sync/failure metadata, validation report models, scene state transitions and invariants. |
| `src/ai_video_generator/models/scene.py` | Backward-compatible re-export of the canonical contract. |
| `src/ai_video_generator/models/generation_job.py` | Job status/stage model with accurate progress and failure records. |
| `src/ai_video_generator/core/constants.py` | Enums and legal transition tables shared by all layers. |
| `src/ai_video_generator/core/exceptions.py` | Typed exception hierarchy including retryable provider errors and blocking assembly errors. |
| `src/ai_video_generator/core/settings.py` | Typed YAML + environment settings loader with conservative validation. |
| `src/ai_video_generator/core/logging.py` | Structured JSON/plain logging with safe contextual fields. |
| `src/ai_video_generator/utils/identifiers.py` | Validated job/scene/artifact identifiers and safe path components. |

### Part 5 — generation

| File | Responsibility |
| --- | --- |
| `src/ai_video_generator/integrations/audiovisual_generation_provider.py` | Provider protocol, capability model, duration selection, generation request/result. |
| `src/ai_video_generator/integrations/narration_synthesizer.py` | Local TTS contract, Windows SAPI adapter, FFmpeg `flite` fallback; no network. |
| `src/ai_video_generator/integrations/deterministic_whiteboard_provider.py` | Deterministic whiteboard frame renderer, exact text manifest, audio/video muxing, measured output. |
| `src/ai_video_generator/integrations/gemini_omni_provider.py` | Optional Gemini Omni Flash adapter using the official Interactions API; capability-honest, no invented duration control. |
| `src/ai_video_generator/integrations/provider_factory.py` | Config-driven provider construction and readiness checks. |
| `src/ai_video_generator/video/media_probe.py` | Safe FFprobe/FFmpeg wrappers, audio level, black detection, frame extraction. |
| `src/ai_video_generator/video/scene_timing.py` | Provider-supported duration mapping and range checks. |
| `src/ai_video_generator/services/scene_generation_service.py` | Required-content checks, provider readiness, idempotency, locking, bounded retries/backoff, storage, measured metadata, scene state updates. |
| `src/ai_video_generator/agents/generation/video_generation_agent.py` | Thin generation agent over the generation service. |

### Part 6 — validation

| File | Responsibility |
| --- | --- |
| `src/ai_video_generator/video/audio_video_validation.py` | Structured validation policy/report builder and all measurable checks. |
| `src/ai_video_generator/video/continuity.py` | Scene-set uniqueness/order and style continuity checks. |
| `src/ai_video_generator/agents/validation/scene_validation_agent.py` | Applies validation to a scene, updates state, persists report/scene. |

### Part 7 — assembly

| File | Responsibility |
| --- | --- |
| `src/ai_video_generator/video/assembly.py` | Assembly options/plan/manifest models, deterministic FFmpeg normalization, title cards, overlays, concat, final metadata. |
| `src/ai_video_generator/services/video_assembly_service.py` | Input gate, work-directory lifecycle, atomic finalization, output verification, job state. |
| `src/ai_video_generator/agents/assembly/video_assembly_agent.py` | Thin assembly agent over the assembly service. |

### Integration, storage, CLI, docs, tests

| File | Responsibility |
| --- | --- |
| `src/ai_video_generator/integrations/storage_provider.py` | Storage Protocol. |
| `src/ai_video_generator/storage/file_storage.py` | Safe local storage implementation. |
| `src/ai_video_generator/storage/artifact_registry.py` | Atomic JSON artifact registry. |
| `src/ai_video_generator/services/storage_service.py` | Artifact-level storage use cases and provenance. |
| `src/ai_video_generator/agents/orchestration/workflow_orchestrator.py` | Generate → validate → retry/reject → assemble → verify flow, job persistence, cancellation checks. |
| `scripts/run_pipeline.py` | End-to-end CLI over a scene-set JSON file. |
| `scripts/check_environment.py` | Read-only environment/capability check. |
| `pyproject.toml` | Packaging metadata, runtime/dev dependencies, pytest configuration. |
| `config/settings.yaml`, `config/video_generation.yaml` | Real conservative defaults. |
| `.env.example` | Documented environment variables, no secrets. |
| `examples/scene_set.sample.json` | Two-scene local demo input. |
| `docs/scene_contract.md`, `docs/video_generation_pipeline.md`, `docs/system_architecture.md`, `docs/agent_workflows.md` | Implemented contracts, provider strategy, commands, limitations. |
| `README.md`, `tests/README.md`, `artifacts/README.md`, `data/README.md` | Status, commands, storage layout, test instructions. |

## 4. Test plan

Framework: pytest, with no network and no paid calls.

- **Unit:** contract invariants and transitions; settings precedence; duration
  mapping; storage key traversal; assembly ordering/input gate; validation report
  blocking semantics.
- **Generation:** mock-provider success; timeout retry; transient retry with
  backoff; permanent failure; missing artifact; unsupported audio capability;
  actual-vs-target duration; idempotent duplicate request; stored metadata.
- **Validation:** valid scene; missing video; missing required audio; corrupt
  media; duration mismatch; audio/video track mismatch; invalid indices/duplicate
  IDs; missing fields; style mismatch; unavailable OCR; blocking prevents
  assembly.
- **Assembly:** index order despite arbitrary input order; intro/outro inclusion;
  missing scene rejection; failed-validation rejection; mixed resolution/fps
  normalization; output metadata verification; render failure leaves job failed;
  temp cleanup; Windows-safe names; storage traversal rejection.
- **Integration:** deterministic provider with a fake WAV narrator through
  generation → validation → assembly; rejected scene blocks output; provider
  failure produces a failed job and diagnostics.

FFmpeg-dependent tests detect the binary and skip with an explicit prerequisite
message if absent. Tiny synthetic 320×180 clips keep runtime low.

## 5. Known limitations to document honestly

- No live Gemini Omni Flash call can be verified without `GEMINI_API_KEY`; the
  adapter is contract-tested with a fake client only. Gemini Omni exposes no
  documented duration control, so its measured output may fall outside 8–15 s and
  validation will report that.
- Generative video text/code/equation accuracy is not verified; OCR is not
  installed, so that check is `unavailable`.
- Semantic narration-to-visual synchronization is not evaluated; only measurable
  track alignment and audio levels are checked.
- Crossfades are not offered because overlapping transitions trim narration;
  `fade_black` is non-overlapping.
- The pipeline is synchronous. There is no HTTP API, worker queue, database, or
  distributed idempotency. Local file locking prevents concurrent duplicate
  generation in one process/host only.
- Windows SAPI/FFmpeg Flite voices are local and may sound synthetic; SAPI is
  preferred, Flite is a fallback.

## 6. Implementation result (2026-10-25)

- Implemented all Part 5-7 modules listed above, plus the minimum shared
  contracts, storage, settings, CLI, and tests.
- `python -m pytest -q` from the repository root: **53 passed** in 41.61 s.
- `python scripts/check_environment.py --json`: `ok: true`, no warnings,
  FFmpeg/FFprobe 8.1.1, libx264/AAC/flite/concat/overlay/fade/silencedetect/
  blackdetect/drawtext detected, Windows SAPI available, `GEMINI_API_KEY` not
  configured, no live Gemini call made.
- Local smoke command: `python scripts\run_pipeline.py --width 640 --height 360
  --fps 12 --no-intro --no-outro --plain-logs --job-id smoke2` with validation
  minimum overrides for 640x360/8 fps. Result: completed, two validated scenes
  (11.5 s and 13.333 s), final MP4 24.853 s, H.264/AAC, 640x360, 12 fps.
- The repository still has no Git metadata. The default provider remains local
  and deterministic; the Gemini adapter has not been exercised against the live
  paid API.
