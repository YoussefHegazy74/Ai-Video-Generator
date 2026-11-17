# System Architecture

**Status:** scene planning, deterministic packaging, the runtime adapter, media
generation/validation, and assembly are integrated as one synchronous local
pipeline. Document ingestion, lesson planning, personalization, a live LLM
adapter, the API server, database, worker queue, authentication, and container
deployment are not implemented.

## Layers and dependency direction

```text
scripts / future API
        |
agents (planning agents, packaging, orchestration, generation, validation, assembly)
        |
services (pipeline factories, generation, validation, assembly, storage)
        |
models + video/whiteboard contracts + integrations contracts/adapters
        |
core (settings, constants, exceptions, logging) + storage + utils
```

Two scene contracts sit at the centre of the graph:

- `video/scene_contract.py` (`ScenePackage`) is the provider-neutral planning
  package produced by `ScenePackager`.
- `video/generation_contract.py` (`SceneSpec`/`SceneSet`) is the runtime media
  contract consumed by generation, validation, and assembly.
- `agents/orchestration/runtime_adapter.py` is the only bridge between them and
  is deliberately deterministic.

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

- `models/lesson_plan.py`, `models/source.py`, `models/scene.py`,
  `models/visual_plan.py`, `models/scene_planning.py`: planning contracts.
- `whiteboard/*`: board layout, drawing primitives, handwriting, highlights,
  continuity, and the combined `WhiteboardPlan`.
- `video/scene_contract.py`: planning `ScenePackage`.
- `video/generation_contract.py`: runtime scene/job-adjacent models.
- `video/scene_timing.py`: planning timeline plus provider duration helpers.
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
- `gemini_llm_client.py`: production structured-output LLM adapter implementing
  the planning ``LLMClient`` protocol; lazy SDK import, JSON Schema validation,
  bounded retries, credential redaction.
- `llm_provider.py`: stable re-export of the LLM boundary and adapter.
- `provider_factory.py`: configuration-driven construction of video providers,
  narrators, and the LLM client.
- `storage_provider.py`: storage Protocol.

### Planning agents

- `agents/scripting/script_agent.py`: `SceneScript` generation.
- `agents/whiteboard/whiteboard_director_agent.py`: `WhiteboardPlan` direction
  with cross-scene continuity.
- `agents/visual_design/visual_design_agent.py`: `VisualPlanningResult`.
- `agents/scene_packaging/`: deterministic `ScenePackage` composition and
  provider-neutral prompt data.
- `agents/orchestration/lesson_pipeline.py`: planning-agent ordering and the
  planning-to-runtime handoff.
- `agents/orchestration/runtime_adapter.py`: planning package to runtime scene
  translation, including provenance and whiteboard text.

### Services and agents

- `services/pipeline_factory.py`: central wiring of settings into planning and
  media components.
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

`GEMINI_API_KEY` is the only credential read, shared by the paid video provider
and the planning LLM adapter. It is stored in a Pydantic `SecretStr` with
`exclude=True` and `repr=False`, is absent from `model_dump()` and
`public_dict()`, and is never logged. Provider metadata and failure details also
redact secret-like keys. LLM selection, model, temperature, timeout, output
budget, and retry policy live under `llm.*` and `AIVG_LLM_*`.

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

`LessonPipeline` runs planning first, then the media orchestrator. Planning
failures happen before a job exists and propagate as typed agent/packaging
errors; media failures are recorded on the job. Planning results are not cached
or resumable.

## Failure handling and idempotency

- Expected failures use typed exceptions with stable codes and sanitized details.
- Planning LLM calls retry only timeouts, connection failures, rate limits, and
  transient 5xx statuses, up to `llm.max_attempts`. Authentication and invalid
  requests are never retried, and the SDK retry loop is disabled.
- Gemini 3.6+/4 reject or ignore sampling parameters; the adapter omits
  temperature for those models and preserves sanitized provider 400 details
  (message, reason, invalid field, request parameter names) without logging
  prompts or keys.
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
