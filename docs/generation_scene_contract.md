# Scene Contract

**Status:** implemented in `src/ai_video_generator/video/scene_contract.py`.
`src/ai_video_generator/models/scene.py` re-exports the same models; there is no
second scene schema.

The contract is the shared boundary between generation, validation, assembly,
persistence, and a future API. It is provider-neutral: provider-specific facts
belong in `GenerationInfo.provider_metadata`, not in new top-level scene fields.

## Top-level models

| Model | Purpose |
| --- | --- |
| `SceneSpec` | One educational scene: authoritative narration, visual plan, target duration, lifecycle state, media references, generation/retry/sync/validation/failure metadata. |
| `SceneSet` | Ordered collection of scenes for one job, plus optional course title and required style. |
| `VisualSpec` | Whiteboard style, instructions, intentional text elements, drawing steps, and presenter prohibition. |
| `MediaAsset` | Measured metadata and storage key for one artifact. |
| `SceneMedia` | Video, optional separate audio, optional deterministic manifest, extras, and embedded-audio flag. |
| `GenerationInfo` | Provider, model, request ID, requested/selected duration, duration-control honesty, text render mode, narration hash, and sanitized metadata. |
| `SyncMetadata` | Measured audio/video duration delta and start offset. Semantic alignment is explicitly separate and not claimed. |
| `RetryInfo` | Attempt count, budget, last error, retryability, and next backoff delay. |
| `FailureDetail` | Stable failure code, stage, retryability, optional validation check ID, and sanitized details. |
| `ValidationCheck` | One pass/warning/fail/unavailable check with expected/observed values and blocking/retryable flags. |
| `ValidationReport` | Per-scene or scene-set checks, policy version, blocking/warning/unavailable counts, and human summary. |

## Required scene fields

A valid `SceneSpec` requires:

- a unique, filesystem-safe `scene_id` (1-64 chars: letters, digits, `.`, `_`,
  `-`; first char alphanumeric);
- a non-negative `scene_index`; indices in a `SceneSet` must be unique,
  contiguous, and start at 0;
- `title`, `learning_objective`, and `narration` with non-blank text;
- a `VisualSpec` with at least one non-blank instruction;
- a `target_duration_seconds` greater than 0 and at most 600;
- `allow_presenter` fixed to `False`;
- `#RRGGBB` background/accent colors.

`SourceReference` page numbers are positive and unique.

## Requested versus measured truth

`SceneSpec.target_duration_seconds` and
`GenerationInfo.requested_duration_seconds` are requests. They are never copied
into `MediaAsset.duration_seconds`. The latter is measured with FFprobe after
generation. `GenerationInfo.selected_duration_seconds` records what the provider
was asked to produce, and `duration_control` states whether the provider actually
supports duration control.

The deterministic local renderer uses the measured narration duration plus a
short tail as the clip duration. Gemini Omni reports `duration_control=False`
because the API has no documented duration control.

## Artifact references

`MediaAsset.artifact_key` is a storage-relative POSIX key. Absolute paths,
drive letters, empty segments, `.`/`..`, and backslash traversal are rejected by
the key validator and again by storage resolution. The contract stores keys, not
host paths.

Measured fields include size, SHA-256, duration, dimensions, fps, codecs,
container, stream counts, sample rate, channels, and whether audio is present.
`SceneMedia.audio_embedded=True` is rejected if the video metadata says it has no
audio.

## Lifecycle

```text
pending -> generating -> generated -> validating -> validated
                                  \-> rejected -> generating
             \-> failed -> generating
validated -> validating -> validated | rejected | failed
```

The legal transitions are defined once in
`src/ai_video_generator/core/constants.py`. A scene cannot skip validation to
become assembly-ready.

State invariants enforced by Pydantic:

- generated/validating/validated/rejected scenes must reference a video and have
  generation metadata;
- a validated scene must include a non-blocking validation report;
- a rejected scene must include a validation report;
- a failed scene must include failure details;
- a generating scene must not retain an old validation report;
- presenter footage is forbidden.

## Assembly gate

`SceneSet.require_assembly_ready()` requires every ordered scene to be
`validated`, have a validation report with no blocking failures, reference a
generated video, and have generation metadata. Assembly then rechecks storage
existence, file size, SHA-256, and readable audio/video streams before rendering.

Blocking validation failures are checks with status `fail`, `blocking=True`, and
severity `error` or `critical`. Warnings block only when
`ValidationPolicy.block_on_warnings` is enabled.

## Versioning and serialization

- `schema_version` is `"1.0"` for scenes and scene sets.
- `VALIDATION_POLICY_VERSION` is `"1.0"`.
- JSON serialization uses `model_dump(mode="json")`.
- Provider metadata and failure details redact keys containing markers such as
  `api_key`, `authorization`, `credential`, `password`, `secret`, or `token`.
- Unknown fields are rejected (`extra="forbid"`) to catch schema drift.

Schema evolution must update the version and provide an explicit migration rather
than silently accepting incompatible payloads.
