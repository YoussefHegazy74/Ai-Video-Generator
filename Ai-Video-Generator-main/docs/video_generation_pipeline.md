# Video Generation Pipeline

**Status:** Parts 5-7 are implemented. Upstream planning/scripting stages are not.
The pipeline is synchronous and runs in one process; there is no HTTP API or
worker queue in this repository.

## Stage flow

```text
SceneSet JSON
  -> generation service (capability check, lock, retries)
  -> measured scene artifact + manifest + scene record
  -> validation agent (measurable checks, structured report)
  -> retry generation when a blocking check is explicitly retryable
  -> assembly gate (every scene validated, hashes/streams verified)
  -> FFmpeg normalization, title cards/overlays, concatenation
  -> FFprobe + audio-level verification
  -> final.mp4 + assembly_manifest.json + completed job
```

The orchestrator persists `job.json` and `scene_set.json` after each meaningful
state change. A failed job retains its failure record and any scene artifacts
already produced.

## Generation

Entry point: `services/scene_generation_service.py`.

1. Validate the scene index against the job's scene count.
2. Reuse a scene that is already `generated`/`validated` only if its stored video
   still exists.
3. Reject a scene already marked `generating`.
4. Check the provider's capabilities. A provider that does not generate
   synchronized audio is rejected; silent scenes are never silently assembled.
5. Call `provider.check_ready()` (credentials/tools only; no paid generation).
6. Ask the provider to resolve a duration. The selected value is recorded
   separately from the measured result.
7. Acquire a job/scene lock under
   `jobs/<job_id>/locks/<index>-<scene_id>.lock` to prevent duplicate local work.
8. Transition the scene to `generating` and render in a job-scoped temporary
   directory.
9. Probe every provider output with FFprobe. Missing/empty files, paths outside
   the temporary directory, missing video, and missing audio are typed provider
   output errors.
10. Retry only retryable `ProviderError` failures, with bounded exponential
    backoff plus jitter. Non-retryable errors fail immediately.
11. Store the video, optional manifest, and optional separate audio through the
    storage abstraction, register provenance, and write `scene.json`.
12. Record measured metadata and `SyncMetadata`; never copy the requested
    duration into measured fields.

The default deterministic renderer synthesizes narration locally, renders
whiteboard frames with Pillow, encodes them with FFmpeg, and muxes AAC audio. The
optional Gemini adapter writes the returned video data or downloads an approved
HTTPS Google media URI. It does not claim duration control.

## Validation

Entry point: `video/audio_video_validation.py`, invoked by
`agents/validation/scene_validation_agent.py`.

Validation is a gate, not a quality score. Checks are `pass`, `warning`, `fail`,
or `unavailable`. Only blocking failures (or warnings when configured to block)
prevent assembly. Reports are persisted as `validation.json`.

Measured checks include:

- artifact existence, minimum size, readability, SHA-256 integrity;
- video/audio stream presence, codec support, container support;
- dimensions and frame rate against policy minimums;
- measured duration versus target warning/failure tolerances and the project
  scene-duration range;
- audio/video stream duration and start-offset alignment;
- audio level (silence detection);
- sampled blank-frame standard deviation and black-frame coverage;
- deterministic text manifest equality against the authoritative scene text;
- style metadata and deterministic background color;
- narration hash integrity and presenter prohibition.

Explicitly unavailable (not fabricated): OCR for generative footage, semantic
narration-to-visual alignment, pixel-level generative style verification, and
source-grounded semantic accuracy. These appear as `unavailable` checks.

When a blocking check is retryable (for example silent narration), the
orchestrator can regenerate the scene up to
`generation.max_validation_attempts`. A rejected scene is cleared of stale media,
generation, sync, and validation metadata before regeneration.

## Assembly

Entry point: `video/assembly.py`, invoked by
`services/video_assembly_service.py`.

The assembly gate requires every scene to be validated and verifies each stored
video's key, size, SHA-256, video stream, and audio stream. Assembly then:

1. Builds an explicit plan in `scene_index` order, with optional intro/outro
   cards and their configured durations.
2. Normalizes each scene to the configured width/height (aspect-preserving scale
   plus pad), fps, pixel format, sample rate, channel layout, and duration.
3. Adds optional scene title/progress overlays and optional audio fades.
4. Renders intro/outro cards with silent audio.
5. Concatenates with the FFmpeg `concat` filter in plan order.
6. Verifies the final file: non-empty, video/audio streams, dimensions, fps,
   H.264/AAC, MP4/MOV container matching configuration, duration within
   tolerance, and non-silent audio.
7. Stores the final video and an assembly manifest containing scene order,
   per-scene hashes/durations, options, and verification results.

Supported transitions are hard `cut` and non-overlapping `fade_black`. Each clip
fades its own edges before concatenation, so narration is not overlapped or
trimmed. Overlapping crossfades are intentionally unsupported.

## Cancellation and failure handling

- `--cancel-file <path>` in the CLI makes cancellation checks consult a flag
  file. The orchestrator checks between scenes and before/after assembly.
- Cancellation before a scene starts leaves it `pending`. Cancellation during
  generation marks that scene `failed` with `pipeline_cancelled` and persists it;
  a later run may regenerate from `failed`.
- Typed exceptions carry stable codes and sanitized details. Provider retries are
  bounded; validation retries are separately bounded; assembly is not silently
  retried.
- Temporary render directories are removed in a `finally` block. Final artifacts
  are stored only after verification.

## CLI

```powershell
python scripts\run_pipeline.py --scene-set examples\scene_set.sample.json --job-id demo1
python scripts\run_pipeline.py --width 640 --height 360 --fps 12 --no-intro --no-outro
```

Exit codes: `0` completed, `1` failed/error, `2` cancelled. The CLI prints a JSON
summary with job status, final artifact paths, per-scene measured durations, and
failure details.

## Honest limitations

- No document ingestion or automatic scene authoring; the CLI consumes a
  pre-built `SceneSet` JSON file.
- No OCR, semantic alignment, or source-grounded evaluation.
- No distributed locking, database, queue, or API.
- Gemini Omni has no documented duration control and has not been called live in
  this environment; its adapter is fake-client tested only.
- The local narrator's measured speech length determines clip duration, so a
  target can be missed even when the renderer succeeds.
