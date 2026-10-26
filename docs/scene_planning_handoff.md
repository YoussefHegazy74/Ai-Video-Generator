# Scene Planning Handoff Guide

**Audience:** planning/personalization, ingestion, provider, packaging, TTS,
rendering, orchestration, and assembly owners.
**Status:** the Script Agent, Whiteboard Director Agent, shared contracts,
Example & Visual Agent, timing, Scene Packager, ScenePromptBuilder, and their
cross-component flow are implemented and tested with a deterministic fake LLM.
No live provider, TTS, renderer, or end-to-end video path has been tested.

This is the short integration guide. The full contract, examples, renderer
requirements, and ownership table are in
[`team_component_contracts.md`](team_component_contracts.md).

---

## 1. What this component delivers

```text
LessonPlan / SceneBrief / SourceExcerpts
                 │
                 ▼
        ScenePlanningInput
                 │
                 ▼
          ScriptAgent.generate()
                 │  SceneScript
                 ▼
  WhiteboardDirectorAgent.generate()
                 │  WhiteboardPlan
                 ▼
        ScenePlanningResult ─────────────┐
                 │                       │
                 ▼                       │
       VisualDesignAgent                 │
                 │  VisualPlanningResult
                 ▼                       │
          ScenePackager ◄────────────────┘
                 │  ScenePackage
                 ▼
        ScenePromptBuilder
                 │  ScenePrompt
                 ▼
     provider-specific generation
```

- **Script Agent:** one scene's narration segments, key points, source
  citations, and delivery notes.
- **Whiteboard Director Agent:** board regions, ordered drawing primitives,
  handwriting cues, highlight cues, and continuity instructions.
- **Combined handoff:** `ScenePlanningResult` bundles the original `SceneBrief`,
  validated `SceneScript`, and `WhiteboardPlan`.
- **Example & Visual Agent:** a scene-scoped `VisualPlan` describing teaching
  visuals, examples, analogies, and exact on-screen text.
- **Scene Packager:** deterministically composes the canonical provider-neutral
  `ScenePackage` from the planning and visual results plus explicit duration
  limits.
- **ScenePromptBuilder:** projects a validated `ScenePackage` into structured,
  provider-neutral `ScenePrompt` data with explicitly labeled planning timing.
- **Not delivered:** live provider calls, TTS, audio, rendering, external visual
  assets, provider-specific payloads, orchestration, or final video.

---

## 2. Minimal integration

The runnable reference is
[`examples/scene_planning_integration.py`](../examples/scene_planning_integration.py).
It builds a valid input, uses a deterministic `LLMClient`, runs both agents,
round-trips the result, and emits the packaging handoff as JSON:

```bash
python examples/scene_planning_integration.py
```

The script prints one object with exactly two top-level keys:

```json
{
  "scene_planning_result": { "...": "validated ScenePlanningResult.to_dict()" },
  "selected_pages_for_packaging": [
    {"start_page": 10, "end_page": 20},
    {"start_page": 22, "end_page": 24}
  ]
}
```

The same flow in code:

```python
from ai_video_generator.agents.scripting import ScriptAgent
from ai_video_generator.agents.whiteboard import WhiteboardDirectorAgent
from ai_video_generator.models.scene_planning import ScenePlanningResult

# planning_input is a ScenePlanningInput built by the planning component.
script = ScriptAgent(llm_client).generate(planning_input)

whiteboard = WhiteboardDirectorAgent(llm_client).generate_for_scene(
    planning_input,
    script,
    previous_plan=None,  # pass the previous scene's plan after the first scene
)

result = ScenePlanningResult(
    lesson_id=planning_input.lesson_id,
    scene=planning_input.scene,
    script=script,
    whiteboard=whiteboard,
)

# Validate the serialization boundary used by packaging.
restored = ScenePlanningResult.from_dict(result.to_dict())
assert restored == result

# Packaging receives both objects: the result does not include selected_pages.
packaging_handoff = {
    "scene_planning_result": result.to_dict(),
    "selected_pages_for_packaging": [
        page_range.to_dict() for page_range in planning_input.selected_pages
    ],
}
```

`llm_client` must implement:

```python
def generate(request: LLMRequest) -> LLMResponse: ...
```

The agents make one provider call each. They do not retry. Replace the example's
`DeterministicLLM` with the shared provider adapter once the provider owner
confirms item 4 below.

---

## 3. What upstream must provide

Required objects and fields are listed in full in the team contract. The short
version:

| Handoff | Required content |
| --- | --- |
| `PageRange` | Inclusive, one-based `start_page` and `end_page`. |
| `SourceReference` | Stable `document_id`, one-based `page_number`; optional `section_title`, `anchor`. |
| `SourceExcerpt` | Stable `source_id`, `reference`, exact `text`; optional `kind`, `caption`. |
| `PersonalizationBrief` | `audience_level`, `language`, `teaching_style`; optional `style_guidance`. |
| `SceneBrief` | Stable `scene_id`, `sequence`, `title`, `objective`, `topics`, `target_duration_seconds`, `source_excerpts`. |
| `ScenePlanningInput` | `lesson_id`, `scene`, `preferences`, `selected_pages`. |

Rules the input contract enforces:

- Every excerpt page must be inside a selected page range.
- Source IDs must be stable and unique within a scene.
- IDs must match `[A-Za-z0-9][A-Za-z0-9._:-]{0,127}` exactly. Do not pad them.
- Text, equations, code, and source excerpts are preserved verbatim.
- `scene_id` and `sequence` are planning-owned; the agents never rename or
  renumber them.

---

## 4. What downstream receives

### Packaging

Use `ScenePlanningResult` plus the scene-scoped `VisualPlanningResult` as the
handoff. The deterministic packager also requires explicit `DurationLimits`:

```python
from ai_video_generator.agents.scene_packaging import (
    ScenePackager,
    ScenePromptBuilder,
)
from ai_video_generator.video.scene_timing import DurationLimits

package = ScenePackager().package(
    planning_result,
    visual_result,
    DurationLimits(minimum_seconds=20.0, maximum_seconds=90.0),
)
prompt = ScenePromptBuilder().build(package)
```

`ScenePackagingInputError` covers mistyped inputs or mismatched scene IDs.
`SceneDurationError` blocks package creation when the planning timeline or target
violates the configured bounds; its `.findings` contains the blocking timing
findings. Canonical `ContractError` cross-reference failures propagate unchanged.
The advisory `timing.target_mismatch` warning remains on `package.timeline`.

Pass the original page selection separately if a downstream consumer needs it.
`ScenePackage` does not duplicate `selected_pages`; it preserves the scene's
source excerpts and their order:

```python
selection_handoff = [
    page.to_dict() for page in planning_input.selected_pages
]
```

`ScenePackage` is provider-neutral and does not replace a provider-specific
payload. `ScenePrompt` is structured prompt data, not a provider API request.

### TTS / timing

- Preserve `segment_id` and segment order.
- Treat `estimated_duration_seconds` as a planning estimate, never as real audio
  timing.
- Return real per-segment timing keyed by `segment_id`.
- Whiteboard actions and highlights anchor to `segment_id`; there are no
  absolute timestamps.
- If TTS splits or merges segments, define an explicit mapping so anchors can be
  re-resolved.
- `ScenePrompt.timing.basis` is explicitly `planning_estimate`; it carries the
  packaging timeline and the fixed planning-estimate notice. Do not relabel it
  as actual audio timing.

### Renderer

Consume `WhiteboardPlan`:

- normalized `0.0`–`1.0` coordinates;
- 1–12 layout regions, each at least `0.001` wide/high and inside the board;
- 1–80 ordered drawing actions (`write_text`, `write_equation`, `draw_line`,
  `draw_arrow`, `draw_rectangle`, `draw_circle`, `draw_path`, `erase`);
- points must lie inside the referenced region;
- handwriting cues reference write actions and follow drawing order;
- highlights target existing non-erase actions and do not precede them;
- `erase` actions are transient and cannot be carried or highlighted;
- continuity modes are `fresh_board`, `continue_board`, and `clear_regions`.

The renderer owns coordinate origin, aspect ratio, font metrics, z-order,
stroke animation, color interpretation, and accessibility. See the team
contract for the full primitive table.

### Orchestration

- Call the Script Agent first.
- Pass its script to the Whiteboard Director Agent.
- Pass `previous_plan=None` only for the first scene.
- Preserve the previous plan for continuity rendering.
- Treat these failures as typed:
  - `AgentInputError` — invalid upstream input;
  - `LLMProviderError` — provider call failed;
  - `AgentOutputError` — malformed or invalid provider output, with `.details`
    and usually `.raw_output`.
- Errors are never converted into fake successful output. Retries are
  orchestration-owned.

---

## 5. Important contract behavior

- `parse_json_object` accepts one complete Markdown JSON code fence. It rejects
  incomplete/unclosed fences, trailing prose, duplicate keys, non-finite
  numbers, and non-object JSON.
- JSON Schemas and runtime validation share the same limits for segment/key-point
  counts, region counts, action/cue counts, point counts, region dimensions, and
  text lengths.
- Some plan-wide rules cannot be expressed in JSON Schema (unique orders,
  cross-references, point-in-region geometry, highlight/continuity semantics).
  Schema-valid output can still fail runtime validation.
- `ScenePlanningResult` and `ScenePackage` do not duplicate `selected_pages`; a
  consumer that needs the original selection must receive it from planning.
- Agent warnings (duration deviation, pacing, language, overlapping regions,
  unanchored segments, long text, action count) do not block agent output.
  Packaging separately blocks on duration-bound errors and retains
  `timing.target_mismatch` as a warning; whether other agent warnings should
  block packaging remains a team decision.

---

## 6. Known limitations

- Tests use a fake LLM only. No live provider, TTS, audio, renderer, or final
  video generation has been exercised.
- Packaging and prompt data are deterministic and provider-neutral; there is no
  provider-specific payload or actual audio timing yet.
- No semantic fact-checking beyond source-ID grounding.
- No provider adapter, retries, persistence, API routes, or job state.
- Renderer semantics (origin, aspect ratio, circle/rectangle interpretation,
  z-order) are provisional.
- External visual assets, image placement, z-order, and on-screen citation
  display are unresolved.
- Source ingestion and upstream planning models are provisional adapters.
- The contracts are frozen dependency-free dataclasses, not Pydantic.

---

## 7. Teammate confirmation checklist: 13 coordination items

### 7.1 How to use this checklist

**None of the 13 open items can be finalized by scene planning alone.** Each
needs an explicit answer from the owner named below. An owner can either:

1. **accept the current provisional contract as written**, or
2. **provide a replacement** (field, shape, or behavior), which requires a
   schema-version bump, contract-doc update, and test/example updates.

Silence is not acceptance. Do not change a stable field in this component
without the owner's written confirmation and the versioning decision in item 12.

Two items (#2 and #12) are **not currently blocking local tests**. They still
need team-wide confirmation before cross-component integration.

| Owner group | Owned open items |
| --- | --- |
| Scene planning alone | None of the 13. Already-resolved internal decisions are listed in 7.4. |
| Upstream planning/personalization | 1, 6 (with TTS/orchestration), 11 |
| Document ingestion | 1, 3 |
| LLM provider/orchestration | 4 |
| TTS/timing | 5, 6 |
| Visual Design | 7 |
| Scene packaging | 7, 8, 12 |
| Renderer | 9 |
| Orchestration/review | 6, 10 |
| Product/review/UI | 13 |
| Team-wide/all consumers | 2, 12 |

### 7.2 Item-by-item confirmation

#### [ ] 1. Provisional upstream models

- **Owner:** planning, personalization, and ingestion.
- **Exact interface/fields:** `LessonPlan(lesson_id, title, selected_pages,
  preferences, scenes)`; `SceneBrief(scene_id, sequence, title, objective,
  topics, target_duration_seconds, source_excerpts)`;
  `PersonalizationBrief(audience_level, language, teaching_style,
  style_guidance)`; `SourceExcerpt(source_id, reference, text, kind, caption)`;
  `PageRange(start_page, end_page)`.
- **Current provisional behavior:** these frozen dataclasses are adapters in
  `models/lesson_plan.py` and `models/source.py`. Planning/ingestion must supply
  every required field exactly as named.
- **Confirm:** Will your components produce these fields, or should scene
  planning adapt to existing models? Specifically confirm the `audience_level`
  enum (`beginner`/`intermediate`/`advanced`), whether `language` is a BCP-47
  tag or display name, the meaning/contiguity of `sequence`, support for gaps in
  `selected_pages`, and support for multiple documents per lesson.
- **Scene planning alone:** No.

#### [ ] 2. Dataclasses versus Pydantic

- **Owner:** team-wide, with every boundary consumer.
- **Exact interface/fields:** every public contract is a frozen, slots-based
  dependency-free dataclass with `from_dict`, `to_dict`, and `to_json`.
  `from_dict` rejects unknown and missing fields. Enums serialize to their string
  values.
- **Current provisional behavior:** no Pydantic dependency is installed or
  required; local tests pass with dataclasses.
- **Confirm:** Does the team accept dependency-free dataclasses at this boundary?
  If Pydantic is required elsewhere, who owns the migration, and will both shapes
  be supported during a compatibility window?
- **Scene planning alone:** No. Not currently blocking local tests.

#### [ ] 3. Source ingestion contract

- **Owner:** document ingestion with planning.
- **Exact interface/fields:** `SourceExcerpt(source_id, reference, text, kind,
  caption)` where `reference` is `SourceReference(document_id, page_number,
  section_title, anchor)` and `kind` is one of `text`, `figure`, `equation`,
  `code`, `table`, `diagram`. `page_number` is one-based and inside a selected
  range; `source_id` is stable and unique within the scene.
- **Current provisional behavior:** excerpts are treated as immutable,
  verbatim text plus provenance. Figure/table/equation/code extraction details
  and multi-document identity rules are not implemented here.
- **Confirm:** What is the canonical excerpt shape, how are stable `source_id`
  values assigned, how are figures/equations/code/tables represented (text? OCR?
  caption only?), what do `caption`/`anchor` mean, and can one lesson mix
  documents? Which page numbering convention does ingestion use?
- **Scene planning alone:** No.

#### [ ] 4. Provider interface

- **Owner:** provider/orchestration.
- **Exact interface/fields:** `LLMClient.generate(request: LLMRequest) ->
  LLMResponse`; `LLMRequest(system_prompt, user_prompt, response_schema_name,
  response_schema, temperature=0.2, metadata={})`;
  `LLMResponse(content, model=None, provider=None)`.
- **Current provisional behavior:** the protocol in
  `agents/scene_planning/llm.py` is local; `integrations/llm_provider.py` is an
  unimplemented placeholder. Each agent makes exactly one call and does not
  retry.
- **Confirm:** Will the provider adapter implement this exact protocol or replace
  it with a shared one? Who owns structured-output/JSON-mode requests, timeouts,
  rate limits, retries, and mapping provider errors to `LLMProviderError`? Must
  `raw_output` be preserved for diagnostics? Is `metadata` sufficient for
  tracing?
- **Scene planning alone:** No.

#### [ ] 5. Segment-level audio alignment

- **Owner:** TTS/timing.
- **Exact interface/fields:** `NarrationSegment.segment_id` is the stable
  alignment key; `estimated_duration_seconds` is an estimate only. TTS must
  preserve `segment_id` and script order and return real start/end/duration
  values keyed by `segment_id`.
- **Current provisional behavior:** there are no absolute timestamps in the
  script or whiteboard plan. Drawing and highlight actions anchor only to
  `segment_id`.
- **Confirm:** What is the exact per-segment timing output schema? Will TTS
  synthesize one audio item per segment, or may it split/merge segments? If it
  splits or merges, what explicit mapping lets whiteboard anchors be re-resolved?
  How are pauses and inserted silence represented?
- **Scene planning alone:** No.

#### [ ] 6. Duration responsibility

- **Owner:** planning, TTS/timing, and orchestration.
- **Exact interface/fields:** `SceneBrief.target_duration_seconds`;
  `NarrationSegment.estimated_duration_seconds`;
  `SceneScript.estimated_duration_seconds` (derived sum);
  `SceneScript.word_count`; warnings `script.duration_deviation` and
  `script.pacing_outlier`.
- **Current provisional behavior:** scene planning estimates and warns only.
  Duration deviation is warned when it exceeds `max(2.0, target * 0.25)`
  seconds; pacing is warned outside 70–220 words per minute. `ScenePackager`
  independently applies explicit `DurationLimits`: out-of-bounds totals or
  targets block packaging, while `timing.target_mismatch` remains advisory. No
  re-planning is triggered automatically.
- **Confirm:** Who owns the production `DurationLimits` values, and are the
  current blocking/advisory rules correct for orchestration? Who decides that
  re-planning is required, and how many attempts are allowed? Is final duration
  measured from TTS audio or from the plan?
- **Scene planning alone:** No.

#### [ ] 7. Visual Design integration

- **Owner:** Visual Design with scene packaging.
- **Exact interface/fields:** `VisualPlan(scene_id, items,
  schema_version="1.0")` with `VisualItem(visual_id, purpose, kind,
  description, segment_ids, key_point_ids, source_ids, exact_text,
  accessibility_notes)`; `WhiteboardPlan.layout` and `.drawing` remain the
  layout/primitive contracts.
- **Current provisional behavior:** the Example & Visual Agent produces the
  semantic visual plan and the Scene Packager composes it with the whiteboard
  plan. No external image/video assets, instructor/avatar representation, asset
  URLs, or renderer placement are modeled.
- **Confirm:** What is the future external-asset input schema? How are assets
  associated with visual items, board regions, or actions, who owns z-order and
  image placement, and does packaging merge assets before or after continuity
  resolution?
- **Scene planning alone:** No.

#### [ ] 8. Canonical scene contract

- **Owner:** Scene Packager.
- **Exact interface/fields:** recommended handoff is
  `ScenePlanningResult(lesson_id, scene, script, whiteboard,
  schema_version="1.0")` plus a `VisualPlanningResult` and explicit
  `DurationLimits`. `ScenePackager.package(...)` returns
  `ScenePackage(lesson_id, scene_id, script, visual, whiteboard, timeline,
  source_excerpts, schema_version="1.0")`.
- **Current provisional behavior:** the canonical provider-neutral package is
  implemented in `video/scene_contract.py`; `ScenePlanningResult` remains the
  combined planning artifact. Packaging is deterministic and makes no provider
  calls. `selected_pages` is not duplicated in the package.
- **Confirm:** Is this package shape the accepted handoff for provider-specific
  packaging, and which consumer owns the mapping from `ScenePackage` to provider
  payloads? Does any consumer require `selected_pages` inside the package? Are
  the schema version and strict unknown-field rejection acceptable?
- **Scene planning alone:** No.

#### [ ] 9. Renderer semantics

- **Owner:** renderer.
- **Exact interface/fields:** normalized `0.0`–`1.0` coordinates;
  `BoardRegion(kind, x, y, width, height)`; `DrawingAction(action_type,
  segment_id, region_id, order, text, points, color, stroke_width,
  duration_seconds, label)`; handwriting/highlight/continuity plans. Primitive
  types are `write_text`, `write_equation`, `draw_line`, `draw_arrow`,
  `draw_rectangle`, `draw_circle`, `draw_path`, `erase`.
- **Current provisional behavior:** scene planning emits normalized primitives
  only. Point pairs for `draw_circle` and `draw_rectangle`, colors, and animation
  estimates have no fixed visual interpretation.
- **Confirm:** Is the coordinate origin top-left? How is aspect ratio mapped?
  Are rectangle points opposite corners and circle points a bounding box or
  center/radius? What are the font/stroke defaults, z-order rules, highlight
  rendering, `erase` semantics, and how are `clear_region_ids` and carried
  actions represented to the renderer?
- **Scene planning alone:** No.

#### [ ] 10. Validation policy

- **Owner:** orchestration and human review.
- **Exact interface/fields:** `ValidationReport(issues)` with `.errors`,
  `.warnings`, `.is_valid`, `.summary`; `ValidationIssue(severity, code,
  message, path)`; typed failures `AgentInputError`, `LLMProviderError`,
  `AgentOutputError` (with `.details`/`.raw_output`), `ScenePlanningError`.
- **Current provisional behavior:** agent warnings are advisory and never block
  agent output; agent errors block output. `ScenePackager` blocks on its
  duration-bound errors but retains `timing.target_mismatch` as a warning. Agent
  warnings include duration deviation, pacing, language mismatch, overlapping
  regions, unanchored narration segments, excessive action count, long written
  text, and a repeated previous scene ID.
- **Confirm:** Should any current agent warnings also block packaging? Who owns
  factual and semantic review, since planning and packaging only ground citation
  IDs? Are validation reports persisted, and who decides retry/re-plan from
  errors?
- **Scene planning alone:** No.

#### [ ] 11. Scene and lesson ID format

- **Owner:** planning, database/API, and all ID producers.
- **Exact interface/fields:** IDs match
  `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$` exactly, including `lesson_id`,
  `scene_id`, `source_id`, `segment_id`, `key_point_id`, `region_id`,
  `action_id`, `cue_id`, and `highlight_id`.
- **Current provisional behavior:** conservative exact-match validation; no
  trimming or normalization; surrounding whitespace is rejected.
- **Confirm:** Are these characters and the 128-character limit compatible with
  planning, database keys, and API routes? Should IDs be globally unique or only
  unique within their parent? Who mints `lesson_id`/`scene_id`?
- **Scene planning alone:** No.

#### [ ] 12. Versioning and compatibility

- **Owner:** all consumers, coordinated by team-wide agreement.
- **Exact interface/fields:** `SCENE_SCRIPT_SCHEMA_VERSION = "1.0"`,
  `WHITEBOARD_PLAN_SCHEMA_VERSION = "1.0"`,
  `SCENE_PLANNING_RESULT_SCHEMA_VERSION = "1.0"`,
  `VISUAL_PLAN_SCHEMA_VERSION = "1.0"`,
  `VISUAL_PLANNING_RESULT_SCHEMA_VERSION = "1.0"`,
  `SCENE_TIMELINE_SCHEMA_VERSION = "1.0"`,
  `SCENE_PACKAGE_SCHEMA_VERSION = "1.0"`, and
  `SCENE_PROMPT_SCHEMA_VERSION = "1.0"`. Each contract has a `schema_version`
  field. Unknown versions are rejected; `from_dict` defaults missing versions to
  `"1.0"`.
- **Current provisional behavior:** only version `1.0` is accepted. There is no
  migration layer.
- **Confirm:** Is fail-on-unknown-version acceptable, or is migration required?
  Who bumps versions and updates schemas/docs/tests? What compatibility window
  applies? Which version does the provider-specific payload use, and how are
  `ScenePackage` and `ScenePrompt` versions negotiated?
- **Scene planning alone:** No. Not currently blocking local tests.

#### [ ] 13. Human review and citation display

- **Owner:** product, review workflow, and UI.
- **Exact interface/fields:** `SceneBrief.source_excerpts` preserves
  `source_id`, `SourceReference(document_id, page_number, section_title,
  anchor)`, exact text, kind, and caption. Script key points cite only
  `source_ids` present in the scene.
- **Current provisional behavior:** provenance is preserved end to end, but
  there is no on-screen citation policy, review state, or approval workflow.
- **Confirm:** Are citations shown on screen? In what format, and should
  internal document paths be suppressed? What review states/approvals are
  required before packaging or rendering? Who owns factual correction after
  review?
- **Scene planning alone:** No.

### 7.3 Per-owner sign-off

- [ ] **Planning/personalization:** items 1, 6, 11 (and 13 inputs).
- [ ] **Ingestion:** items 1, 3.
- [ ] **Provider/orchestration:** items 4, 6, 10.
- [ ] **TTS/timing:** items 5, 6.
- [ ] **Visual Design:** item 7.
- [ ] **Scene Packager:** items 7, 8, 12, plus acceptance of the separate
      `selected_pages_for_packaging` handoff.
- [ ] **Renderer:** item 9.
- [ ] **Product/review/UI:** items 10, 13.
- [ ] **Team-wide:** items 2, 12.

### 7.4 Decisions already finalized inside scene planning

These were resolved during the hardening pass and are **not** part of the 13
open coordination items: exact text/ID preservation, schema/runtime limit
alignment, source-subset checks in `ScenePlanningResult`, highlight
target/order rules, continuity erase/cleared-region rules, point-in-region
geometry, and strict JSON parsing.

The decision not to duplicate `selected_pages` in `ScenePlanningResult` is also
final for the current contract. Packaging must consume it separately, as shown
in section 2.

---

## 8. Verification commands

Run from the repository root:

```bash
python -m pytest -q -p no:cacheprovider
python -m compileall -q src tests
git diff --check
python examples/scene_planning_integration.py
```

Tests require no API keys, paid services, or internet access. The cross-component
test in `tests/unit/test_scene_pipeline_integration.py` covers the visual-plan →
`ScenePackage` → `ScenePrompt` sequence with a deterministic fake LLM.

For the full contract, examples, renderer requirements, and the source
coordination table, see [`team_component_contracts.md`](team_component_contracts.md).

The example verifies only the current public scene-planning APIs and the
selected-page handoff. It does not validate a live provider, TTS timing,
rendering, provider-specific packaging, or end-to-end video generation. Scene
packaging and prompt data are covered separately by deterministic unit and
cross-component tests.
