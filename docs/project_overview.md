# Project Overview

**Status:** scene planning, deterministic packaging, scene generation,
measurable validation, and final-video assembly are implemented and integrated.
Document ingestion, lesson planning, personalization, a live LLM adapter, an HTTP
API, and worker infrastructure remain placeholders. The full entry point accepts
a `LessonPlan` plus per-scene planning artifacts; it does not yet create them from
a book or PDF.

## Product vision

The AI Educational Video Generator is intended to convert a selected page range
from a book or PDF into a personalized, long-form educational video. The
presentation is a digital whiteboard lesson with synchronized narration. No
instructor face or human avatar is shown.

The implemented default provider is a **local deterministic whiteboard
renderer, not an AI video model**. It prioritizes exact technical text, code, and
equations over generative visual variety and requires no credentials. An optional
Gemini Omni Flash adapter targets `gemini-omni-1.1-flash`; it requires
`GEMINI_API_KEY`, makes paid API calls, has no documented duration control, and
has only been contract-tested with a fake client in this environment.

## Target users

- Learners who want a narrated, whiteboard-style explanation of technical
  material.
- Educators and technical authors who can supply or review structured scene
  content.
- Developers evaluating the generation, validation, and assembly stages before
  upstream lesson-authoring features exist.

## Current user journey

```text
LessonPlan + planning artifacts
  -> package and adapt each scene
  -> generate each scene with synchronized narration
  -> measure and validate the media
  -> retry explicitly retryable failures or reject the job
  -> normalize, title/overlay, and concatenate validated scenes
  -> verify and store final.mp4 + assembly_manifest.json
```

The CLI writes job state, per-scene media, validation reports, and the final
artifact under `artifacts/jobs/<job_id>/`. Progress and cancellation are
synchronous and cooperative; there is no background service or web UI.

## Inputs and configuration

- A canonical `SceneSet` JSON file with ordered scene specifications, narration,
  visual requirements, target durations, and style metadata.
- YAML defaults under `config/`, overridden by documented `AIVG_*` environment
  variables.
- Optional `GEMINI_API_KEY` only when the paid `gemini_omni` provider is selected.
- Local FFmpeg/FFprobe and a local TTS path for the default provider.

## Scope

Implemented:

- Planning contracts for source excerpts, lesson/scene briefs, scripts,
  whiteboard direction, visual plans, and planning timelines.
- Script, Whiteboard Director, and Example & Visual agents behind an injected
  structured LLM boundary, plus deterministic scene packaging and a
  planning-to-runtime adapter.
- A typed runtime scene/job contract with legal state transitions.
- Local deterministic and optional Gemini Omni generation adapters.
- Retry, idempotency, cancellation, measured metadata, and artifact storage.
- Audio/video validation with explicit pass, warning, fail, and unavailable
  results.
- Deterministic assembly with title cards, overlays, hard cuts, and
  non-overlapping fades to black.

Not implemented:

- PDF/book ingestion, content normalization, lesson planning, and
  personalization.
- A live LLM provider adapter.
- Per-segment narration timing and whiteboard primitive rendering in the local
  renderer.
- OCR, semantic narration-to-visual alignment, and source-grounded accuracy
  scoring.
- HTTP API, database, worker queue, distributed locking, authentication, or
  deployment automation.
- Generative-text verification or a claim that Gemini Omni honors requested
  durations.

## Key constraints

- **Source fidelity:** the deterministic renderer draws authoritative scene text
  exactly. Generative footage is not treated as text-accurate; OCR and semantic
  accuracy are reported as unavailable.
- **Language and narration:** narration is synthesized locally for the default
  provider. The scene contract carries language metadata, but upstream
  translation/localization is not implemented.
- **Duration:** validation enforces a project scene-duration range and target
  tolerances against measured media. Requested durations are never reported as
  actual durations. The local narrator's speech length determines the measured
  clip length.
- **Teaching style:** scenes are ordered, titled, and rendered on a whiteboard
  background with optional progress overlays. Style continuity is checked at the
  metadata level, not by a generative style model.
- **Safety and secrets:** only `GEMINI_API_KEY` is read; it is excluded from
  dumps, repr, and logs. Storage keys are validated and constrained to the
  storage root.

## Glossary

- **Project/job:** one end-to-end run over a scene set, identified by a job ID and
  persisted as `job.json`.
- **Scene set:** the ordered, validated collection of scenes and shared style
  metadata.
- **Scene:** one narrated whiteboard segment with a target duration and visual
  requirements.
- **Artifact:** a stored media or JSON output referenced by a storage key rather
  than an absolute path.
- **Provider:** an adapter that produces synchronized audio/video for a scene.
- **Validation:** a gate over measurable properties such as streams, duration,
  dimensions, frame rate, audio level, and manifest equality.
- **Assembly:** normalization, titling/overlay, ordering, concatenation, and final
  verification of validated scene clips.
