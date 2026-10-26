# System Architecture

**Status:** Parts 5-7 are implemented as a synchronous local pipeline. There is no
API server, database, worker queue, authentication, or container deployment in
this repository.

## Layers and dependency direction

```text
scripts / future API
        |
agents (orchestration, generation, validation, assembly)
        |
services (generation, validation, assembly, storage)
        |
video contracts/utilities + integrations contracts/adapters
        |
core (settings, constants, exceptions, logging) + storage + utils
```

Dependencies point downward. Domain models do not import services or adapters.
Provider adapters depend on the provider contract, not on the orchestrator.
Storage consumers use artifact keys through `ArtifactStorageService`; they do not
construct absolute paths.

### Core

- `core/constants.py`: scene/job enums, legal transitions, severity rules, and
  schema/policy versions.
- `core/exceptions.py`: typed exception hierarchy with stable codes, retryability,
  structured details, and optional causes.
- `core/logging.py`: structured JSON or plain logging with safe fields.
- `core/settings.py`: typed YAML + `AIVG_*` environment settings, path resolution,
  and secret handling.
- `utils/identifiers.py`, `utils/paths.py`: safe identifiers and storage keys.

### Contracts and media

- `video/scene_contract.py`: canonical scene/job-adjacent models.
- `video/media_probe.py`: argument-list FFmpeg/FFprobe wrappers with timeouts,
  captured diagnostics, frame extraction, audio-level measurement, and black
  detection.
- `video/audio_video_validation.py`: policy and measurable validation checks.
- `video/continuity.py`: scene-set uniqueness/order/style checks.
- `video/assembly.py`: assembly options/plan/manifest and deterministic renderer.

### Integrations

- `audiovisual_generation_provider.py`: provider Protocol, capabilities, duration
  selection, request/result models.
- `deterministic_whiteboard_provider.py`: default local renderer.
- `gemini_omni_provider.py`: optional real AI adapter.
- `narration_synthesizer.py`: local Windows SAPI and FFmpeg `flite` adapters.
- `provider_factory.py`: configuration-driven construction.
- `storage_provider.py`: storage Protocol.

### Services and agents

- `services/scene_generation_service.py`: capability checks, locking, retries,
  probing, storage, measured metadata, scene persistence.
- `services/video_assembly_service.py`: assembly input gate, temporary lifecycle,
  final verification, manifest storage.
- `services/storage_service.py`: artifact-level storage and provenance.
- `agents/validation/scene_validation_agent.py`: validation gate and report/state
  persistence.
- `agents/orchestration/workflow_orchestrator.py`: synchronous job state machine,
  progress, cancellation, retry/reject, and assembly integration.

## Configuration and secrets

Precedence: model defaults < `config/settings.yaml` <
`config/video_generation.yaml` < `AIVG_*` environment variables. Relative paths
resolve against the repository root, not the current working directory.

`GEMINI_API_KEY` is the only credential read. It is stored in a Pydantic
`SecretStr` with `exclude=True` and `repr=False`, is absent from `model_dump()` and
`public_dict()`, and is never logged. Provider metadata and failure details also
redact secret-like keys.

## Persistence and artifacts

`LocalFileStorage` validates keys, resolves them inside the storage root, writes
atomically with `os.replace`, and keeps temporary work directories under the
configured temp root. `ArtifactRegistry` is an atomic JSON provenance log for
single-process use. Job/scene manifests are written as JSON artifacts.

```text
artifacts/
  artifact_registry.json
  .work/
  jobs/<job_id>/
    job.json
    scene_set.json
    scenes/<index>-<scene_id>/{video,manifest,validation,scene}.json/mp4
    final/{final.mp4,assembly_manifest.json}
```

The artifact registry is not a distributed database. Concurrent writers from
multiple processes are not supported.

## Job execution and progress

The orchestrator owns a `GenerationJob` with explicit stages (`preparing`,
`generating`, `validating`, `assembling`, `verifying`, `completed`, `failed`,
`cancelled`). Progress percentages come from completed work units, not fabricated
timers. Stage history is persisted. Cancellation is cooperative and checked
between scenes and around assembly.

There is no background execution. A future API/worker can call
`WorkflowOrchestrator.run()` and persist/stream the same job model, but that
integration does not exist yet.

## Failure handling and idempotency

- Expected failures use typed exceptions with stable codes and sanitized details.
- Provider failures are retried only when marked retryable, with bounded
  exponential backoff and jitter.
- Validation can request regeneration only through explicitly retryable blocking
  checks, bounded by `max_validation_attempts`.
- Local generation locks prevent duplicate work on one host. Locks are released
  in `finally`; a crashed process can leave a stale lock that must be removed
  manually after verifying no generation is running.
- Scene state transitions are legal-transition checked; illegal transitions raise
  `SceneStateError`.
- Assembly verifies the final media before storing it, so a failed render does not
  become a completed job.

## Observability and cost control

- Structured logs include job/scene IDs, stage, provider, attempt counts, measured
  durations, and error codes; secrets are not logged.
- The environment check reports installed tools/capabilities and whether the
  Gemini key is configured, without making a paid call.
- The default provider is local and free. The Gemini provider is opt-in and paid;
  it is never selected by default, and automated tests use a fake client.
