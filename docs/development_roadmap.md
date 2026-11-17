# Development Roadmap

**Status:** Parts 5-7 plus scene planning, packaging, the planning-to-runtime
adapter, and the production Gemini LLM adapter are implemented and integrated.
Ingestion, lesson planning, personalization, and the serving layer are still
placeholders.

This roadmap distinguishes the implemented local pipeline from the remaining
product work. A checked item means code, tests, and user-facing documentation
exist in this repository.

## Completed: planning and Parts 5-7

- [x] **Scene planning contracts and agents.** Script Agent, Whiteboard Director
  Agent, Example & Visual Agent, shared source/lesson/script/whiteboard/visual
  contracts, prompts, schemas, semantic validation, and tests.
- [x] **Scene packaging and timing.** Deterministic `ScenePackage`, planning
  timeline, explicit duration limits, and provider-neutral prompt data.
- [x] **Integration.** `SceneRuntimeAdapter`, `LessonPlanningPipeline`,
  `LessonPipeline`, centralized pipeline factories, a full lesson CLI, a
  deterministic demo lesson, and a real local render-to-MP4 integration test.
- [x] **Production LLM adapter.** Gemini structured output behind the existing
  `LLMClient`, JSON Schema validation, bounded retries, credential redaction,
  configuration, factory injection, a live smoke command, and deterministic
  fake-SDK tests. Live API behavior is not yet verified.
- [x] **Shared foundations.** Typed settings, structured logging, stable
  exceptions, safe identifiers and paths, local storage, and an artifact
  registry.
- [x] **Part 5 - scene generation.** Provider contract and capability checks;
  local deterministic whiteboard provider with Pillow frames, local TTS, and
  FFmpeg muxing; optional Gemini Omni adapter; measured FFprobe metadata;
  bounded retries with backoff/jitter; local duplicate-generation locks;
  cancellation checks; artifact and scene persistence.
- [x] **Part 6 - scene validation.** Policy-driven checks for media presence,
  streams, codecs, dimensions, frame rate, measured duration versus target,
  audio/video alignment, audio level, blank/black frames, text-manifest equality,
  style metadata, hashes, and presenter prohibition. Unimplemented semantic
  checks are reported as `unavailable`.
- [x] **Part 7 - video assembly.** Validation gate, source hash/stream checks,
  normalization, title cards, scene labels/progress overlays, explicit scene
  ordering, hard cuts and non-overlapping fades to black, final FFprobe and audio
  verification, and a persisted assembly manifest.
- [x] **Integration.** Synchronous generate -> validate -> retry/reject ->
  assemble orchestration, job progress, cooperative cancellation, CLI, and
  environment check.
- [x] **Quality gate.** No-network/no-paid-call pytest suite, sample scene set,
  configuration files, and architecture/contract/workflow documentation.

## Next: upstream authoring pipeline

These modules currently contain docstrings only and are not invoked by the
pipeline. The planning agents and packaging are implemented but need a live LLM
adapter and a real lesson planner to run from a document.

1. **Ingestion and normalization.** Accept PDFs/books and page ranges, extract
   text and figures, preserve source references, and record extraction quality.
   Exit criterion: a deterministic normalized document with page-level
   provenance and tests for malformed inputs.
2. **Lesson planning.** Derive objectives, prerequisites, and an ordered lesson
   outline grounded in the normalized source. Exit criterion: reviewable plan
   with citations and explicit uncertainty.
3. **Scripting and personalization.** Produce narration, examples, pacing, and
   audience adaptations without inventing source claims. Exit criterion:
   schema-valid scripts with source links and duration estimates.
4. **Live LLM verification and cost controls.** Run the smoke test with a real
   key, then one full lesson plan; add token/cost accounting and a per-job budget.
   Exit criterion: a verified live planning run with recorded cost.
5. **Segment-level timing and renderer fidelity.** Return real per-segment TTS
   timings and render whiteboard primitives/handwriting so cues synchronize to
   narration instead of uniform reveal.

## Later: serving and operations

- **API and review workflow.** Expose job submission/status, artifact retrieval,
  and human review of plans/scripts before paid generation.
- **Background execution.** Add a worker queue and durable job store; replace the
  single-host lock with a distributed idempotency mechanism only when multiple
  workers exist.
- **Persistence and retention.** Introduce a database or managed artifact store
  with retention, quotas, and deletion semantics.
- **Observability and cost control.** Add metrics/traces, budget caps, and
  per-provider cost accounting.
- **Evaluation.** Add OCR where justified, semantic narration-to-visual
  alignment, source-grounded factuality checks, and human quality review.
- **Accessibility and localization.** Captions, transcripts, translated
  narration, and locale-aware voices.

## Current risks and open decisions

- **Generative fidelity:** Gemini Omni does not expose documented duration
  control, and generative text/code/equation accuracy is unverified. The local
  deterministic renderer remains the default for technical accuracy.
- **Duration control:** the local narrator's speech length determines measured
  duration; target durations guide pacing but may be missed. Validation rejects
  out-of-range results rather than hiding the mismatch.
- **Semantic validation:** OCR, semantic alignment, and source-grounded accuracy
  are unavailable; they must not be presented as passing checks.
- **Concurrency:** storage and locks are designed for one process/host. A stale
  lock after a crash requires manual cleanup.
- **Serving architecture:** API shape, queue technology, database, and review
  workflow are undecided because no serving layer exists yet.
- **Transitions:** overlapping crossfades are intentionally unsupported because
  they can trim narration; `cut` and non-overlapping `fade_black` are the
  supported options.

## Verification baseline

- `python -m pytest -q` from the repository root: 321 tests, no network or paid
  calls (FFmpeg-dependent tests skip when the tools are absent).
- `python scripts/check_environment.py --json`: local media/TTS capability report
  without a paid Gemini call.
- `python scripts/run_lesson_pipeline.py --lesson-plan
  examples/demo_lesson/lesson_plan.json --planning-dir examples/demo_lesson/planning
  --job-id demo`: integrated local lesson pipeline through final assembly.
- `python scripts/llm_smoke_test.py --json`: optional one-request live Gemini
  check; not part of the automated suite.
- `python scripts/run_pipeline.py --scene-set examples/scene_set.sample.json
  --job-id demo1`: runtime-only local smoke path.

See `README.md`, `docs/implementation_plan_parts_5_7.md`, and
`docs/video_generation_pipeline.md` for exact commands and limitations.
