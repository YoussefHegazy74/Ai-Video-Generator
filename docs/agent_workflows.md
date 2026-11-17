# Agent Workflows

**Status:** the Script, Whiteboard Director, Example & Visual, packaging, and
runtime agents are implemented. Ingestion, personalization, and lesson planning
modules remain placeholders.

## Implemented flow

```text
LessonPlanningPipeline (per scene, in lesson order)
  -> ScriptAgent.generate(ScenePlanningInput) -> SceneScript
  -> WhiteboardDirectorAgent.generate_for_scene(..., previous_plan) -> WhiteboardPlan
  -> VisualDesignAgent.generate(VisualPlanningInput) -> VisualPlanningResult
  -> ScenePackager.package(ScenePlanningResult, VisualPlanningResult, DurationLimits)
       -> ScenePackage
  -> SceneRuntimeAdapter.adapt_lesson(...) -> SceneSet
  -> WorkflowOrchestrator
       -> SceneGenerationService / VideoGenerationAgent
       -> SceneValidationAgent
            -> retry generation if a blocking check is retryable
            -> otherwise fail the job
       -> VideoAssemblyService / VideoAssemblyAgent
       -> completed job + final video + manifests
```

`LessonPipeline` composes the planning half, the adapter, and the runtime
orchestrator. All agents and services are injected; factories in
`services/pipeline_factory.py` centralize construction from configuration. When no
client is injected, `build_planning_pipeline` constructs the configured Gemini
adapter; tests and offline runs keep injecting fakes or pre-generated artifacts.

### Planning agents

All three planning agents share one injected ``LLMClient``. The production
implementation is `integrations/gemini_llm_client.py`: it sends the agent's JSON
Schema as a Gemini structured-output request, validates the returned object, and
maps provider failures to typed planning errors. Agents never import the SDK.

- **Script Agent** makes one structured LLM call per scene and returns validated
  narration segments, key points, and source citations. It does not retry or
  fabricate citations; bounded transport retries belong to the adapter.
- **Whiteboard Director** consumes the validated script and the previous scene's
  plan, and returns board regions, ordered drawing primitives, handwriting cues,
  highlights, and continuity instructions.
- **Example & Visual Agent** returns a pedagogical visual plan with required exact
  text; it does not own layout or rendering.
- **Scene Packager** is deterministic and provider-neutral. It blocks on duration
  errors and retains target-mismatch warnings on the planning timeline.
- **Runtime adapter** translates the package into the runtime contract. It copies
  narration text, target duration, whiteboard text, and all source provenance, and
  rejects runtime-unsafe scene IDs.

### Runtime workflow orchestrator

`agents/orchestration/workflow_orchestrator.py` owns the synchronous job state
machine:

1. Create a `GenerationJob`, persist `job.json` and `scene_set.json`, and emit a
   progress callback.
2. Run structural/style continuity checks. Blocking failures fail the job before
   any paid/local generation work.
3. For each scene in index order:
   - check cancellation;
   - generate through the generation service;
   - persist the updated scene set and mark the scene generated;
   - validate through the validation agent;
   - mark the scene validated, retry generation when validation explicitly
     recommends it, or fail the job with the blocking check ID.
4. Check cancellation again before assembly.
5. Assemble and verify, then mark the job completed with final artifact keys.
6. Persist the job after every meaningful transition.

The orchestrator catches unexpected exceptions at its boundary, records a
sanitized `FailureDetail`, persists the failed job, and re-raises so the CLI can
report the error. It does not swallow exceptions.

### Generation agent/service

The thin agent delegates to `SceneGenerationService`. The service is responsible
for provider readiness, capability enforcement, local idempotency locking,
retry/backoff, output-path containment, FFprobe measurement, artifact storage,
and scene state updates. It never invents duration or stream metadata.

### Validation agent

`SceneValidationAgent` transitions a generated scene to `validating`, runs the
`SceneValidator`, persists `validation.json`, and then:

- transitions to `validated` when there are no blocking failures;
- transitions to `rejected` and records a `FailureDetail` (including the first
  blocking check ID) otherwise.

Warnings do not block unless policy says they do. `unavailable` checks are
informational and never treated as passes.

### Assembly agent/service

The thin agent delegates to `VideoAssemblyService`. The service refuses
unvalidated or structurally inconsistent scene sets, verifies source artifact
hashes/streams, renders in a temporary directory, verifies the final media, and
stores the final video plus manifest.

## Retry and rejection policy

- Generation retries: provider `ProviderError` with `retryable=True`, up to
  `generation.max_attempts`, with exponential backoff and jitter.
- Validation retries: only when a blocking validation check is marked
  `retryable=True`, up to `generation.max_validation_attempts`.
- A rejected scene is cleared of stale media/generation/validation metadata before
  regeneration.
- Non-retryable provider errors and non-retryable validation failures fail the job
  immediately with structured details.
- Cancellation is cooperative. It is not converted into a retry.

## State and persistence

- Scene states and legal transitions are defined in `core/constants.py`.
- `job.json` contains status, stage, progress, counters, failures, stage history,
  metadata, and final keys.
- `scene_set.json` contains the full canonical scene state, including measured
  media metadata and validation reports.
- `scene.json` is the per-scene record after generation/validation.
- `validation.json` is the structured report.
- `assembly_manifest.json` records output hash/size/duration, scene order,
  per-scene hashes/durations, options, and verification results.

## Not implemented

Document ingestion, personalization, lesson planning, and a live LLM provider
adapter are not implemented. There is no human review UI, model-based lesson
planning, or task queue. The full CLI starts from a `LessonPlan` plus validated
per-scene planning artifacts; the runtime CLI starts from a `SceneSet` JSON file.
