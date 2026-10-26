# AI Educational Video Generator

> **Status:** Parts 5-7 are implemented: scene generation, measurable scene
> validation, and deterministic final-video assembly. Ingestion, lesson planning,
> scripting, whiteboard direction, personalization, the HTTP API, and worker
> infrastructure remain placeholders. The default provider is a **local
> deterministic whiteboard renderer, not an AI video model**.

## What is implemented

- A canonical Pydantic scene/job contract with legal state transitions and
  requested-versus-measured duration separation.
- A provider contract with capability checks, plus two adapters:
  - `deterministic_whiteboard` (default): Pillow-rendered whiteboard frames,
    local TTS narration, and FFmpeg muxing. It renders authoritative scene text
    exactly and needs no credentials.
  - `gemini_omni` (optional): a real AI adapter for `gemini-omni-1.1-flash`.
    It requires `GEMINI_API_KEY`, makes paid API calls, and exposes **no
    documented duration control**. It has only been contract-tested with a fake
    client; no live call was made in this environment.
- Retryable generation failures with bounded exponential backoff and jitter,
  local duplicate-generation locking, and persisted retry/failure metadata.
- Measurable validation: streams, codecs, dimensions, frame rate, duration,
  audio/video track timing, audio level, black/blank frames, deterministic text
  manifest equality, style metadata, and file hashes. OCR, semantic
  narration-to-visual alignment, and source-accuracy scoring are reported as
  `unavailable` rather than fabricated.
- Deterministic assembly: normalize every clip, optional intro/outro title cards,
  scene labels/progress overlays, `cut` or non-overlapping `fade_black`
  transitions, explicit scene-index ordering, FFprobe verification, and a
  persisted assembly manifest.
- A synchronous generate -> validate -> retry/reject -> assemble orchestrator
  with progress callbacks, cooperative cancellation, and job/scene persistence.
- Local storage with path-traversal protection, atomic writes, an artifact
  registry, and job-scoped temporary directories.
- A CLI (`scripts/run_pipeline.py`) and a read-only environment check
  (`scripts/check_environment.py`).

## Prerequisites

- Python 3.11+ (this checkout was verified with Python 3.11.9).
- FFmpeg and FFprobe on `PATH` (verified with FFmpeg 8.1.1; `libx264`, AAC,
  `concat`, `overlay`, `fade`, `silencedetect`, `blackdetect`, `drawtext`, and
  the `flite` source are required by the local path).
- A local TTS path for the default provider: Windows SAPI (verified available)
  or an FFmpeg build with the `flite` filter. No cloud TTS is used.
- Optional: `GEMINI_API_KEY` only if you deliberately select the paid
  `gemini_omni` provider.

## Windows quick start

```powershell
cd D:\Ai-Video-Generator\Ai-Video-Generator-main
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python scripts\check_environment.py
python scripts\run_pipeline.py --scene-set examples\scene_set.sample.json --job-id demo1
```

The scripts also insert `src/` on `sys.path`, so they work from a source checkout
without installing the package. `pytest` may not be on `PATH`; use
`python -m pytest`.

The sample scene set requests two scenes. With the local narrator on this
machine, the measured durations were about 11.5 s and 13.3 s, matching the
updated 11 s / 13 s targets within validation tolerance. The final file is
written under `artifacts/jobs/<job_id>/final/`.

Useful CLI options:

```powershell
python scripts\run_pipeline.py --help
python scripts\run_pipeline.py --width 640 --height 360 --fps 12 --no-intro --no-outro --job-id smoke
python scripts\run_pipeline.py --provider gemini_omni --job-id gemini-demo   # paid; needs GEMINI_API_KEY
```

`--cancel-file <path>` enables cooperative cancellation: create the file to
request a stop between work units.

## Configuration

Configuration precedence, lowest to highest:

1. Pydantic defaults.
2. `config/settings.yaml`.
3. `config/video_generation.yaml` (validation and assembly sections).
4. `AIVG_*` environment variables.

Relative paths resolve against the repository root. The only credential read by
the application is `GEMINI_API_KEY`; it is stored as a `SecretStr`, excluded from
dumps and repr, and never logged. Copy `.env.example` to `.env` for local use and
never commit a real `.env`.

Important defaults:

| Area | Default |
| --- | --- |
| Provider | `deterministic_whiteboard` |
| Validation duration range | 8-15 s per scene |
| Validation minimums | 640x360, 8 fps, audio required |
| Assembly | 1280x720, 24 fps, H.264/AAC MP4, `fade_black`, intro/outro on |
| Generation retries | 3 attempts, 1 s base, 8 s cap, 0.25 s jitter |
| Validation retries | 2 attempts |

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
      scene.json
    final/
      final.mp4
      assembly_manifest.json
```

Artifact references are storage keys, never absolute paths. The final video is
stored only after FFprobe/FFmpeg verification and audio-level checks pass.

## Tests

```powershell
python -m pytest -q
```

The suite uses synthetic 320x180 clips, a fake deterministic provider, and a fake
Gemini client. It makes no network calls and no paid API calls. FFmpeg-dependent
tests skip with an explicit prerequisite message when FFmpeg/FFprobe are absent.
On this checkout: **53 passed** (local Windows run). See `tests/README.md`.

## Current limitations

- Only Parts 5-7 are implemented. There is no document ingestion, lesson
  planning, scripting, personalization, API server, database, worker queue, or
  authentication.
- The pipeline is synchronous and single-process. The local generation lock
  prevents duplicate work on one host; it is not a distributed lock.
- The default renderer is deterministic, not AI. Its narration pacing determines
  the measured clip duration; the target duration only guides pacing.
- Gemini Omni has no documented duration control. A generated clip may fall
  outside the validation range and be rejected. Generative text/code/equation
  accuracy is not verified.
- Semantic narration-to-visual alignment, OCR, and source-grounded accuracy are
  not implemented and are reported as unavailable.
- Crossfades are intentionally unsupported because overlapping transitions can
  trim narration. Only hard cuts and non-overlapping fades to black are offered.
- No live Gemini call was performed because no `GEMINI_API_KEY` is configured.

## Repository layout

```text
config/                 Runtime YAML defaults (no secrets)
docs/                   Contracts, pipeline, architecture, workflows, plan
examples/               Sample scene-set JSON
scripts/                Environment check and pipeline CLI
src/ai_video_generator/
  agents/               Generation, validation, assembly, orchestration
  core/                 Constants, exceptions, logging, settings
  integrations/         Provider contracts/adapters, narration, storage protocol
  models/               Scene re-export and generation job model
  services/             Generation, validation, assembly, storage services
  storage/              Local file storage and artifact registry
  utils/                Identifiers and safe path helpers
  video/                Scene contract, probing, validation, continuity, assembly
tests/                  Unit and integration tests
artifacts/              Generated outputs (ignored by Git)
data/                   Reserved for future ingestion/upstream data
```

## Documentation

- `docs/implementation_plan_parts_5_7.md` - approved plan and implementation
  record.
- `docs/scene_contract.md` - canonical schema, states, and invariants.
- `docs/video_generation_pipeline.md` - generation, validation, and assembly
  behavior.
- `docs/system_architecture.md` - layers, storage, configuration, and failure
  handling.
- `docs/agent_workflows.md` - implemented orchestration and agent boundaries.
