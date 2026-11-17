# AI Educational Video Generator

> **Status:** the scene-planning stack and the local media pipeline are
> integrated end to end. A lesson plan plus validated scene-planning artifacts can
> be packaged, adapted, generated, validated, and assembled into a verified MP4
> with synchronized local narration. Document ingestion, lesson planning,
> personalization, a live LLM adapter, the HTTP API, and worker infrastructure
> remain unimplemented or placeholders.
>
> The default renderer is a **local deterministic whiteboard renderer**, not an
> AI video model. No live LLM or Gemini call has been executed.

## Vision

Turn selected pages from a book or PDF into a personalized, long-form educational
video presented as a screen-shared digital whiteboard lesson. An instructor's
voice explains the material while handwriting, diagrams, equations, code,
highlights, and pointer motion appear progressively. No instructor face or body
is shown.

The intended inputs are a document and page range, audience level, language,
target duration, and a predefined or custom teaching style. Only the downstream
planning-package and media stages are implemented today; see
[Current limitations](#current-limitations).

## What is implemented

| Area | Status | Location |
| --- | --- | --- |
| Domain contracts (source, lesson plan, script, whiteboard, visual, timing) | Implemented, dependency-free dataclasses | `src/ai_video_generator/models/`, `whiteboard/`, `video/` |
| Script Agent | Implemented, injected structured LLM boundary | `agents/scripting/` |
| Whiteboard Director Agent | Implemented: layout, primitives, handwriting, highlights, continuity | `agents/whiteboard/` |
| Example & Visual Agent | Implemented: pedagogical visual plan and required text | `agents/visual_design/` |
| Planning timeline and duration limits | Implemented, planning estimates only | `video/scene_timing.py` |
| Scene Packager | Implemented, deterministic `ScenePackage` composition | `agents/scene_packaging/` |
| Planning-to-runtime adapter | Implemented: `ScenePackage` to `SceneSpec`/`SceneSet` | `agents/orchestration/runtime_adapter.py` |
| Lesson pipeline (planning to video) | Implemented orchestration with injected components | `agents/orchestration/lesson_pipeline.py` |
| Generation service | Implemented: provider capabilities, retries, locks, measured probing | `services/scene_generation_service.py` |
| Deterministic whiteboard renderer | Implemented: Pillow frames, local TTS, FFmpeg mux | `integrations/deterministic_whiteboard_provider.py` |
| Gemini Omni adapter (optional) | Contract-tested only; no live call; paid; no duration control | `integrations/gemini_omni_provider.py` |
| Scene validation | Implemented: streams, codecs, duration, audio, black/blank frames, exact-text manifest | `video/audio_video_validation.py` |
| Final assembly | Implemented: normalization, title cards, overlays, cuts/fades, FFprobe verification | `video/assembly.py` |
| Storage | Implemented: atomic local storage, artifact registry, locks, job temp dirs | `storage/`, `services/storage_service.py` |
| Configuration | Implemented: YAML + `AIVG_*` overrides, typed settings | `core/settings.py`, `config/` |
| Document ingestion | Not implemented (placeholder modules) | `ingestion/` |
| Lesson planning / personalization agents | Not implemented (placeholders) | `agents/planning/`, `agents/personalization/` |
| Live LLM provider adapter | Not implemented; `LLMClient` is a protocol only | `agents/scene_planning/llm.py` |
| HTTP API | Not implemented (placeholder routes/schemas) | `api/` |
| Distributed workers / database | Not implemented | — |

## Integrated pipeline

```text
LessonPlan + SourceExcerpts                       [planning input, authored or future planner]
        |
        v
ScriptAgent -> SceneScript
        |
        v
WhiteboardDirectorAgent (+ previous plan) -> WhiteboardPlan
        |
        v
VisualDesignAgent -> VisualPlanningResult
        |
        v
ScenePackager -> ScenePackage  (script + whiteboard + visual + timeline + sources)
        |
        v
SceneRuntimeAdapter -> SceneSet (runtime SceneSpec per scene, contiguous indices)
        |
        v
WorkflowOrchestrator
   generate (provider) -> probe -> validate -> retry/reject -> assemble -> verify
        |
        v
artifacts/jobs/<job_id>/final/final.mp4 + assembly_manifest.json
```

Two contracts intentionally coexist:

- `ScenePackage` (`video/scene_contract.py`) is the provider-neutral planning
  package. It preserves segment IDs, key points, whiteboard anchors, source
  excerpts, and the planning timeline.
- `SceneSpec`/`SceneSet` (`video/generation_contract.py`) is the runtime media
  contract. It carries authoritative narration text, target duration, lifecycle
  state, media references, validation, and failure metadata.

`SceneRuntimeAdapter` is the only bridge between them. It never rewrites
narration, and it copies every source excerpt into runtime source references.

## Installation

Requirements:

- Python 3.11+ (verified here on Python 3.14.4)
- FFmpeg and FFprobe on `PATH` (verified with FFmpeg 8.0.1) with `libx264`,
  AAC, `concat`, `overlay`, `fade`, `silencedetect`, `blackdetect`, `drawtext`,
  and preferably the `flite` audio source
- A local TTS path: Windows SAPI or FFmpeg `flite`
- Optional: `GEMINI_API_KEY` plus `google-genai` only for the paid Gemini provider

```bash
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python scripts/check_environment.py
```

The scripts also insert `src/` on `sys.path`, so a source checkout works without
installation. Use `python -m pytest` when `pytest` is not on `PATH`.

## Configuration

Precedence, lowest to highest:

1. Pydantic defaults in `core/settings.py`.
2. `config/settings.yaml`.
3. `config/video_generation.yaml` (validation and assembly).
4. `AIVG_*` environment variables (see `.env.example`).

Relative paths resolve against the repository root. The only credential read is
`GEMINI_API_KEY`; it is stored as a `SecretStr` and excluded from dumps and logs.
Copy `.env.example` to `.env` for local use and never commit a real `.env`.

Important defaults:

| Area | Default |
| --- | --- |
| Provider | `deterministic_whiteboard` (local, no credentials) |
| Planning scene duration | 5-120 s per scene |
| Validation scene duration | 5-120 s per scene; 1.5 s warning / 3 s failure drift |
| Validation minimums | 640x360, 8 fps, audio required |
| Assembly | 1280x720, 24 fps, H.264/AAC MP4, `fade_black`, intro/outro on |
| Generation retries | 3 attempts, 1 s base, 8 s cap, 0.25 s jitter |
| Validation retries | 2 attempts |

`config/agents.yaml` documents agent responsibilities and transitions; it is not
a runtime switch.

## Running

### Full lesson pipeline from planning artifacts

```bash
python scripts/run_lesson_pipeline.py \
  --lesson-plan examples/demo_lesson/lesson_plan.json \
  --planning-dir examples/demo_lesson/planning \
  --job-id demo
```

Use `--plan-only` to package and adapt scenes without touching media tools. The
default provider renders locally; the final video lands under
`artifacts/jobs/<job_id>/final/`.

`examples/demo_lesson/` was produced by `examples/build_demo_lesson.py`, which
drives the **real** planning agents and packager with a fixed fake LLM response
queue. It is a deterministic integration fixture, not a lesson planner. Regenerate
it with:

```bash
python examples/build_demo_lesson.py
```

### Media pipeline from a ready `SceneSet`

```bash
python scripts/run_pipeline.py \
  --scene-set examples/scene_set.sample.json \
  --job-id smoke
```

Useful options for both scripts: `--provider`, `--width`, `--height`, `--fps`,
`--no-intro`, `--no-outro`, `--cancel-file <path>` (cooperative cancellation
between work units), `--plain-logs`, and `--log-level`.

### Optional paid provider

```bash
export GEMINI_API_KEY=...
python scripts/run_pipeline.py --provider gemini_omni --job-id gemini-demo
```

Gemini Omni exposes no documented duration control, so validation measures the
returned clip and may reject it for duration drift. No live Gemini call has been
verified in this repository.

## Storage layout

```text
artifacts/
  artifact_registry.json
  .work/                                  # job-scoped temporary render directories
  jobs/<job_id>/
    job.json
    scene_set.json
    scenes/<index>-<scene_id>/
      video.mp4
      manifest.json
      validation.json
      scene.json                          # runtime scene incl. source references
    final/
      final.mp4
      assembly_manifest.json
```

Artifact references are storage keys, never absolute paths. The final video is
stored only after FFprobe/FFmpeg verification.

## Testing

```bash
python -m pytest -q
python -m compileall -q src tests examples scripts
python examples/scene_planning_integration.py
```

The suite uses a deterministic fake LLM, synthetic media, and a fake provider.
It makes no network or paid calls. FFmpeg-dependent tests skip when the tools are
absent. The integrated test suite covers planning contracts, agent validation,
packaging, runtime adaptation, configuration, media generation/validation with
synthetic clips, and a full lesson-pipeline run through real FFmpeg assembly.

## Current limitations

- No document ingestion, lesson planning, or personalization implementation; the
  integrated pipeline starts from an authored `LessonPlan` and planning artifacts.
- No live LLM adapter. A provider implementing `LLMClient` must be supplied to
  the planning factory before an end-to-end run can start from a lesson plan
  alone.
- The planning-to-runtime adapter intentionally flattens narration into one
  runtime string. Per-segment audio timing (`segment_id` anchors) is not carried
  into provider generation, so progressive whiteboard cues cannot yet be
  synchronized to real narration segments.
- The deterministic renderer draws intentional text, a cursor, and a progress
  bar. It does not render the whiteboard's geometric primitives, handwriting
  paths, highlight shapes, or cross-scene carried actions.
- `SceneSpec.source_references` preserves document/page/excerpt provenance but not
  `SourceKind`, capture references, captions, or on-screen citation display.
- Gemini Omni has no duration control and has only been contract-tested with a
  fake client.
- Semantic narration-to-visual alignment, OCR, source-accuracy scoring, and
  factual review are reported as unavailable rather than fabricated.
- The pipeline is synchronous and single-process. Locks are local, not
  distributed.
- No API, database, worker queue, authentication, or human-review workflow.
- Runtime scene IDs are stricter than planning IDs (64 chars, no `:`); the
  adapter rejects incompatible IDs with a clear error.
- Planning runs are not cached or resumable. Both CLIs start from pending scenes, so
  re-running a job regenerates its media. A persisted `scene_set.json` can be fed
  back to `scripts/run_pipeline.py`, but resume behavior is not covered by tests or
  a documented workflow.

## Repository layout

```text
config/                 Runtime YAML defaults (no secrets)
docs/                   Contracts, architecture, workflow, handoff, integration report
examples/               Sample scene set, deterministic demo lesson, planning example
scripts/                Environment check, media CLI, full lesson CLI
src/ai_video_generator/
  agents/               Planning agents, packaging, validation, assembly, orchestration
  api/                  HTTP interface placeholders
  core/                 Settings, constants, exceptions, logging
  ingestion/            Document ingestion placeholders
  integrations/         LLM boundary, audiovisual providers, narration, storage protocol
  models/               Source, lesson, script, visual, planning, and job models
  personalization/      Style catalog placeholders
  services/             Use-case services and pipeline factories
  storage/              Local storage and artifact registry
  utils/                Identifiers and safe path helpers
  video/                Planning package, runtime scene contract, timing, probe, validation, assembly
  whiteboard/           Board layout, drawing, handwriting, highlighting, continuity
tests/                  Unit and integration tests
artifacts/              Generated outputs (ignored by Git)
data/                   Reserved runtime data directories
```

## Documentation

- `docs/full_team_integration_report.md` - what was integrated, tested, and left open.
- `docs/system_architecture.md` - layers, dependency direction, storage, configuration.
- `docs/video_generation_pipeline.md` - planning-to-media stages and checks.
- `docs/scene_contract.md` - planning `ScenePackage` contract.
- `docs/generation_scene_contract.md` - runtime `SceneSpec`/`SceneSet` contract.
- `docs/team_component_contracts.md` - Script/Whiteboard component boundary.
- `docs/scene_planning_handoff.md` - handoff checklist and open decisions.
- `docs/agent_workflows.md` - agent roles and orchestration boundaries.
- `docs/implementation_plan_parts_5_7.md` - original generation/validation/assembly plan.
