# Team Component Contracts: Script Agent and Whiteboard Director Agent

**Status:** implemented component contract with provisional upstream adapters.
**Owner:** scene-planning component (Script Agent, Whiteboard Director Agent, and
their shared contracts, prompts, schemas, validation, and tests).
**Scope:** one planned lesson scene at a time.
**Last verified:** unit tests with a deterministic fake LLM only. No provider,
TTS, renderer, packaging, or end-to-end video test has been run.

This document is the coordination boundary between this component and the
planning/personalization, ingestion, provider, packaging, generation, and
assembly components. It describes what is implemented now, the exact interfaces,
and the decisions that still require team agreement.

---

## 1. Scope

This component implements:

- the **Script Agent**, which turns one planned scene into validated narration
  segments, key teaching points, source citations, and delivery notes;
- the **Whiteboard Director Agent**, which turns a validated scene script into
  board regions, ordered drawing primitives, progressive handwriting cues,
  highlight cues, and cross-scene continuity instructions;
- shared scene-planning contracts, JSON Schemas, versioned prompts, semantic
  validation, typed errors, and dependency-free unit tests for both agents.

The implementation lives in:

| Responsibility | Files |
| --- | --- |
| Script Agent | `src/ai_video_generator/agents/scripting/script_agent.py` |
| Whiteboard Director Agent | `src/ai_video_generator/agents/whiteboard/whiteboard_director_agent.py` |
| Shared agent support (LLM boundary, prompts, schemas, validation, errors) | `src/ai_video_generator/agents/scene_planning/` |
| Source and page provenance | `src/ai_video_generator/models/source.py` |
| Provisional lesson/personalization input adapters | `src/ai_video_generator/models/lesson_plan.py` |
| Scene planning input and script output | `src/ai_video_generator/models/scene.py` |
| Combined scene-planning handoff | `src/ai_video_generator/models/scene_planning.py` |
| Whiteboard contracts | `src/ai_video_generator/whiteboard/board_layout.py`, `drawing_instructions.py`, `handwriting_plan.py`, `highlighting.py`, `visual_continuity.py`, `plan.py` |
| Tests | `tests/conftest.py`, `tests/unit/test_contracts.py`, `test_script_agent.py`, `test_whiteboard_director_agent.py`, `test_prompts_and_schemas.py`, `test_scene_planning_flow.py`, `test_hardening_regressions.py` |

## 2. Explicit non-goals

This component does **not**:

- implement lesson planning, scene sequencing, or duration allocation;
- implement personalization, style resolution, or audience analysis;
- implement PDF/document ingestion, OCR, page extraction, or source
  normalization;
- implement the Visual Design Agent, choose external images, or own visual
  asset generation;
- implement the Scene Packager, canonical provider-facing scene contract, video
  model adapter, narration/TTS, audio generation, rendering, validation of
  rendered media, or final assembly;
- implement orchestration, retries, persistence, API routes, job state, or human
  review;
- select or configure an LLM provider;
- add a runtime dependency or require network access in tests.

The whiteboard plan intentionally contains drawing **primitives**, not renderer
instructions. Mapping normalized coordinates and primitives to a specific
renderer is downstream work.

---

## 3. Public API and exact signatures

### 3.1 Agents

```python
class ScriptAgent:
    agent_name: str = "script_agent"
    prompt_version: str = "1.0.0"

    def __init__(self, llm_client: LLMClient, *, temperature: float = 0.2) -> None: ...

    def generate(self, planning_input: ScenePlanningInput) -> SceneScript: ...


class WhiteboardDirectorAgent:
    agent_name: str = "whiteboard_director_agent"
    prompt_version: str = "1.0.0"

    def __init__(self, llm_client: LLMClient, *, temperature: float = 0.2) -> None: ...

    def generate(self, direction_input: WhiteboardDirectionInput) -> WhiteboardPlan: ...

    def generate_for_scene(
        self,
        planning_input: ScenePlanningInput,
        script: SceneScript,
        *,
        previous_plan: WhiteboardPlan | None = None,
    ) -> WhiteboardPlan: ...
```

`LLMClient` is a runtime-checkable protocol:

```python
class LLMClient(Protocol):
    def generate(self, request: LLMRequest) -> LLMResponse: ...


LLMRequest(
    system_prompt: str,
    user_prompt: str,
    response_schema_name: str,
    response_schema: Mapping[str, Any],
    temperature: float = 0.2,
    metadata: Mapping[str, str] = field(default_factory=dict),
)

LLMResponse(content: str, model: str | None = None, provider: str | None = None)
```

### 3.2 Contract constructors

All contracts are frozen, slots-based, dependency-free dataclasses with
`from_dict`, `to_dict`, and `to_json`. The signatures below are the supported
public constructors; keyword arguments are recommended for readability.

```python
PageRange(start_page: int, end_page: int)

SourceReference(
    document_id: str,
    page_number: int,
    section_title: str | None = None,
    anchor: str | None = None,
)

SourceExcerpt(
    source_id: str,
    reference: SourceReference,
    text: str,
    kind: SourceKind = SourceKind.TEXT,
    caption: str | None = None,
)

PersonalizationBrief(
    audience_level: AudienceLevel,
    language: str,
    teaching_style: str,
    style_guidance: str = "",
)

SceneBrief(
    scene_id: str,
    sequence: int,
    title: str,
    objective: str,
    topics: tuple[str, ...],
    target_duration_seconds: float,
    source_excerpts: tuple[SourceExcerpt, ...],
)

LessonPlan(
    lesson_id: str,
    title: str,
    selected_pages: tuple[PageRange, ...],
    preferences: PersonalizationBrief,
    scenes: tuple[SceneBrief, ...],
)

ScenePlanningInput(
    lesson_id: str,
    scene: SceneBrief,
    preferences: PersonalizationBrief,
    selected_pages: tuple[PageRange, ...],
)

NarrationSegment(
    segment_id: str,
    text: str,
    estimated_duration_seconds: float,
    key_point_ids: tuple[str, ...],
)

KeyPoint(
    key_point_id: str,
    text: str,
    source_ids: tuple[str, ...],
    importance: KeyPointImportance = KeyPointImportance.CORE,
)

SceneScript(
    scene_id: str,
    language: str,
    segments: tuple[NarrationSegment, ...],
    key_points: tuple[KeyPoint, ...],
    delivery_notes: tuple[str, ...] = (),
    schema_version: str = "1.0",
)

Point(x: float, y: float)

BoardRegion(
    region_id: str,
    kind: BoardRegionKind,
    x: float,
    y: float,
    width: float,
    height: float,
    label: str | None = None,
)

BoardLayout(regions: tuple[BoardRegion, ...])

DrawingAction(
    action_id: str,
    action_type: DrawingActionType,
    segment_id: str,
    region_id: str,
    order: int,
    text: str | None = None,
    points: tuple[Point, ...] = (),
    color: str | None = None,
    stroke_width: float | None = None,
    duration_seconds: float | None = None,
    label: str | None = None,
)

DrawingPlan(actions: tuple[DrawingAction, ...])

HandwritingCue(
    cue_id: str,
    action_id: str,
    mode: HandwritingMode,
    order: int,
    reveal_duration_seconds: float | None = None,
)

HandwritingPlan(cues: tuple[HandwritingCue, ...])

HighlightCue(
    highlight_id: str,
    target_action_id: str,
    segment_id: str,
    style: HighlightStyle,
    order: int,
    color: str | None = None,
    duration_seconds: float | None = None,
)

HighlightPlan(cues: tuple[HighlightCue, ...])

ContinuityPlan(
    mode: ContinuityMode,
    carry_over_action_ids: tuple[str, ...] = (),
    clear_region_ids: tuple[str, ...] = (),
    notes: tuple[str, ...] = (),
)

WhiteboardPlan(
    scene_id: str,
    layout: BoardLayout,
    drawing: DrawingPlan,
    handwriting: HandwritingPlan,
    highlights: HighlightPlan,
    continuity: ContinuityPlan,
    presentation_notes: tuple[str, ...] = (),
    schema_version: str = "1.0",
)

WhiteboardDirectionInput(
    planning_input: ScenePlanningInput,
    script: SceneScript,
    previous_plan: WhiteboardPlan | None = None,
)

ScenePlanningResult(
    lesson_id: str,
    scene: SceneBrief,
    script: SceneScript,
    whiteboard: WhiteboardPlan,
    schema_version: str = "1.0",
)
```

Convenience constructors:

```python
ScenePlanningInput.from_lesson_plan(lesson_plan: LessonPlan, scene_id: str)
ScenePlanningInput.from_dict(data)
SceneScript.from_dict(data)
WhiteboardPlan.from_dict(data)
ScenePlanningResult.from_dict(data)
# Every other contract also exposes from_dict(data).
```

Validation and parsing helpers:

```python
validate_planning_input(planning_input: ScenePlanningInput) -> ValidationReport
validate_script(script: SceneScript, planning_input: ScenePlanningInput) -> ValidationReport
validate_whiteboard(
    plan: WhiteboardPlan,
    script: SceneScript,
    planning_input: ScenePlanningInput,
    previous_plan: WhiteboardPlan | None = None,
) -> ValidationReport
parse_json_object(content: str) -> dict[str, Any]
```

`ValidationReport` exposes `.issues`, `.errors`, `.warnings`, `.is_valid`, and
`.summary`. Warnings never block agent output; errors do.

### 3.3 Representative Script Agent input

`ScenePlanningInput.to_dict()` for one scene (abbreviated excerpt text; all
fields shown are required unless marked optional):

```json
{
  "lesson_id": "lesson-001",
  "scene": {
    "scene_id": "scene-001",
    "sequence": 1,
    "title": "Neural Networks",
    "objective": "Explain layers and training to a beginner.",
    "topics": ["layers", "training", "prediction"],
    "target_duration_seconds": 30.0,
    "source_excerpts": [
      {
        "source_id": "src-1",
        "reference": {
          "document_id": "book-1",
          "page_number": 12,
          "section_title": "Neural Networks",
          "anchor": null
        },
        "text": "A neural network is organized in layers. Each layer contains connected units that pass information forward.",
        "kind": "text",
        "caption": null
      },
      {
        "source_id": "src-2",
        "reference": {
          "document_id": "book-1",
          "page_number": 23,
          "section_title": "Training",
          "anchor": null
        },
        "text": "Training adjusts the network's weights to reduce error. After training, the network can make predictions for new inputs.",
        "kind": "text",
        "caption": null
      }
    ]
  },
  "preferences": {
    "audience_level": "beginner",
    "language": "en",
    "teaching_style": "football",
    "style_guidance": "Use football analogies without changing the source facts."
  },
  "selected_pages": [
    {"start_page": 10, "end_page": 20},
    {"start_page": 22, "end_page": 24}
  ]
}
```

### 3.4 Representative Script Agent output

```json
{
  "schema_version": "1.0",
  "scene_id": "scene-001",
  "language": "en",
  "segments": [
    {
      "segment_id": "seg-1",
      "text": "A neural network is like a football team: each layer contains players who pass information forward to the next group.",
      "estimated_duration_seconds": 12.0,
      "key_point_ids": ["kp-1"]
    },
    {
      "segment_id": "seg-2",
      "text": "Training is the practice session; the network adjusts its weights after each attempt so its passes become more accurate.",
      "estimated_duration_seconds": 10.0,
      "key_point_ids": ["kp-2"]
    },
    {
      "segment_id": "seg-3",
      "text": "After enough practice, the model can predict the next play from new input.",
      "estimated_duration_seconds": 8.0,
      "key_point_ids": ["kp-2"]
    }
  ],
  "key_points": [
    {
      "key_point_id": "kp-1",
      "text": "A neural network is organized in connected layers.",
      "source_ids": ["src-1"],
      "importance": "core"
    },
    {
      "key_point_id": "kp-2",
      "text": "Training adjusts weights so the model can make predictions.",
      "source_ids": ["src-2"],
      "importance": "core"
    }
  ],
  "delivery_notes": ["Pause briefly before the prediction summary."]
}
```

Derived properties are intentionally not stored twice on `SceneScript`:
`estimated_duration_seconds` (sum of segment estimates), `narration` (ordered
segment text joined for display), `word_count`, and `cited_source_ids`.

### 3.5 Representative Whiteboard Director input

`WhiteboardDirectionInput` wraps the planning input and the validated script:

```python
direction_input = WhiteboardDirectionInput(
    planning_input=planning_input,
    script=script,
    previous_plan=None,  # or the previous scene's WhiteboardPlan
)
```

`WhiteboardDirectionInput` rejects a script whose `scene_id` does not match the
planning input.

### 3.6 Representative Whiteboard Director output

```json
{
  "schema_version": "1.0",
  "scene_id": "scene-001",
  "layout": {
    "regions": [
      {
        "region_id": "main",
        "kind": "content",
        "x": 0.0,
        "y": 0.0,
        "width": 1.0,
        "height": 1.0
      }
    ]
  },
  "drawing": {
    "actions": [
      {
        "action_id": "act-1",
        "action_type": "write_text",
        "segment_id": "seg-1",
        "region_id": "main",
        "order": 1,
        "text": "Neural network = connected layers"
      },
      {
        "action_id": "act-2",
        "action_type": "write_text",
        "segment_id": "seg-2",
        "region_id": "main",
        "order": 2,
        "text": "Training adjusts weights"
      },
      {
        "action_id": "act-3",
        "action_type": "draw_arrow",
        "segment_id": "seg-2",
        "region_id": "main",
        "order": 3,
        "points": [
          {"x": 0.2, "y": 0.4},
          {"x": 0.5, "y": 0.4}
        ]
      },
      {
        "action_id": "act-4",
        "action_type": "write_text",
        "segment_id": "seg-3",
        "region_id": "main",
        "order": 4,
        "text": "Predict the next play"
      }
    ]
  },
  "handwriting": {
    "cues": [
      {"cue_id": "hw-1", "action_id": "act-1", "mode": "handwrite", "order": 1},
      {"cue_id": "hw-2", "action_id": "act-2", "mode": "handwrite", "order": 2},
      {"cue_id": "hw-3", "action_id": "act-4", "mode": "handwrite", "order": 3}
    ]
  },
  "highlights": {
    "cues": [
      {
        "highlight_id": "hl-1",
        "target_action_id": "act-2",
        "segment_id": "seg-2",
        "style": "marker",
        "order": 1
      }
    ]
  },
  "continuity": {
    "mode": "fresh_board",
    "carry_over_action_ids": [],
    "clear_region_ids": [],
    "notes": []
  },
  "presentation_notes": ["Reveal one idea at a time."]
}
```

---

## 4. Required upstream fields

The planning/personalization/ingestion owners must supply these values. Fields
marked optional may be omitted in `from_dict`; the constructor defaults apply.

| Object | Required fields | Optional fields |
| --- | --- | --- |
| `PageRange` | `start_page`, `end_page` (inclusive, one-based) | — |
| `SourceReference` | `document_id`, `page_number` | `section_title`, `anchor` |
| `SourceExcerpt` | `source_id`, `reference`, `text` | `kind`, `caption` |
| `PersonalizationBrief` | `audience_level`, `language`, `teaching_style` | `style_guidance` |
| `SceneBrief` | `scene_id`, `sequence`, `title`, `objective`, `topics`, `target_duration_seconds`, `source_excerpts` | — |
| `LessonPlan` | `lesson_id`, `title`, `selected_pages`, `preferences`, `scenes` | — |
| `ScenePlanningInput` | `lesson_id`, `scene`, `preferences`, `selected_pages` | — |

Upstream invariants enforced on construction:

- `scene_id`, `lesson_id`, `document_id`, `source_id`, `segment_id`,
  `key_point_id`, `region_id`, `action_id`, `cue_id`, `highlight_id` match
  `[A-Za-z0-9][A-Za-z0-9._:-]{0,127}` exactly; surrounding whitespace and
  trailing newlines are rejected.
- `sequence >= 1`; lesson scenes are ordered by strictly increasing `sequence`.
- `source_excerpts` is non-empty and source IDs are unique within a scene.
- Every source excerpt page is inside at least one `selected_pages` range.
- `source_id` values are stable for the lesson; agents never generate them.
- `topics` has 1–30 items; text fields have the limits documented in the
  contracts and schemas.
- Educational text, equations, code, and source excerpts are stored verbatim.
  They are never trimmed or normalized by `from_dict`.

---

## 5. Shared identifiers and schema ownership

| Identifier | Created/owned by | Consumers | Stability rule |
| --- | --- | --- | --- |
| `lesson_id` | Planning | Both agents, packaging | Never recreated by scene planning |
| `scene_id` | Planning | Both agents, packaging, rendering | Never renamed or renumbered by scene planning |
| `sequence` | Planning | Orchestration, continuity, packaging | Never changed by scene planning |
| `source_id` | Ingestion/planning | Script Agent, packaging, review | Stable for the lesson; never invented by an agent |
| `page_number` | Ingestion | Both agents, packaging | One-based, inside a selected page range |
| `segment_id` | Script Agent | Whiteboard Director, TTS/alignment, packaging, validation | Stable after script generation |
| `key_point_id` | Script Agent | Script validation, whiteboard direction | Stable within the script |
| `region_id` | Whiteboard Director | Drawing actions, continuity, renderer | Stable within the whiteboard plan |
| `action_id` | Whiteboard Director | Handwriting, highlights, continuity, renderer | Stable within the plan and usable across adjacent scenes |
| `cue_id` / `highlight_id` | Whiteboard Director | Renderer, validation | Unique within their plan component |

**Schema/version ownership:**

- `SCENE_SCRIPT_SCHEMA_VERSION` and the `SceneScript` shape: this component.
- `WHITEBOARD_PLAN_SCHEMA_VERSION` and whiteboard shapes: this component.
- `SCENE_PLANNING_RESULT_SCHEMA_VERSION` and combined handoff: this component.
- Provider-facing scene package and its version: Scene Packager owner.
- Final video/scene contract: generation/assembly owners.

Unknown schema versions are rejected rather than silently accepted. Any shape
change must bump the relevant version and be coordinated with consumers.

---

## 6. Source provenance requirements

The component treats source provenance as immutable data:

1. `PageRange` is inclusive and one-based. `selected_pages` is carried into
   `ScenePlanningInput`.
2. `ScenePlanningInput` rejects any `SourceExcerpt` whose page is outside all
   selected ranges.
3. `SceneBrief.source_excerpts` must have unique `source_id` values.
4. The Script Agent prompt lists the selected pages and the exact allowed
   `source_ids`, and serializes each excerpt with its `document_id`,
   `page_number`, `section_title`, `anchor`, `kind`, and `caption`.
5. A script key point may cite only source IDs supplied for that scene. Unknown
   IDs are an agent output error.
6. `ScenePlanningResult` re-checks that every key-point `source_id` exists in the
   bundled `SceneBrief`, so a caller cannot combine a valid script with a
   different scene and silently fabricate citations.
7. `ScenePlanningResult` stores the original `SceneBrief` unchanged, including
   excerpts and page references, for downstream review and packaging.
8. Source excerpts are untrusted data in prompts. Instructions embedded inside
   source text are not followed.
9. No agent derives page numbers or citations from model memory. Citation
   display, if required, must use the preserved `SourceReference`.

`ScenePlanningResult` intentionally does not duplicate the lesson's
`selected_pages`; the selection belongs to `LessonPlan`/`ScenePlanningInput`.
Packaging that needs to display or re-check the selection must receive it from
planning. This is a known handoff limitation, not an agent behavior gap: both
agents only receive excerpts already checked against the selected ranges.

---

## 7. Drawing-plan contract expected by the renderer

Coordinates are normalized to the inclusive range `0.0`–`1.0`. The current
contract does **not** define the coordinate origin or aspect-ratio mapping;
those remain renderer-owned decisions (see the decisions table).

### 7.1 Board layout

- `BoardLayout.regions` has 1–12 unique `region_id` values.
- Each `BoardRegion` is a rectangle with `x`, `y` in `[0, 1]` and `width`,
  `height` in `[0.001, 1]`.
- `x + width <= 1.0` and `y + height <= 1.0`.
- Overlapping regions are allowed by the structural contract but reported as a
  semantic warning. Renderers should not assume non-overlap.
- `BoardRegionKind` is one of `title`, `content`, `example`, `diagram`, `code`,
  `equation`, `summary`.

### 7.2 Drawing actions

`DrawingPlan.actions` has 1–80 actions. `action_id` values are unique and
`order` values are unique, start at 1, and are strictly increasing in list
order.

| `action_type` | Required payload | Geometry / text rules |
| --- | --- | --- |
| `write_text` | `text` | No points. Text is the exact text to reveal; max 2000 chars. |
| `write_equation` | `text` | No points. Text is the exact equation to reveal; max 2000 chars. |
| `draw_line` | `points` | Exactly 2 points. |
| `draw_arrow` | `points` | Exactly 2 points (start, end). |
| `draw_rectangle` | `points` | Exactly 2 points (opposite corners). |
| `draw_circle` | `points` | Exactly 2 points (bounding box or center/radius; renderer semantics unresolved). |
| `draw_path` | `points` | At least 2 points; ordered polyline. |
| `erase` | `points` | At least 2 points; transient action with no persistent element. |

All actions also carry `segment_id`, `region_id`, and `order`. Every point must
lie inside the referenced region (with a small floating-point tolerance).
Optional fields:

- `color`: free-form string, max 32 chars; renderer interprets it.
- `stroke_width`: `0.5`–`20.0`.
- `duration_seconds`: `0.1`–`600.0`; a planning/animation estimate, not audio
  timing.
- `label`: human-readable annotation, max 200 chars.
- Non-write `text`: optional label/annotation, max 300 chars.

Write actions may be referenced by handwriting cues. Shape/path actions are
visual elements and may be highlighted. `erase` actions are transient: they
cannot be targeted by a highlight and cannot be carried into the next scene.

### 7.3 Handwriting cues

- `HandwritingPlan.cues` has 0–80 cues.
- Each cue references an existing write action; `action_id` is unique across
  cues.
- `order` values are unique and strictly increasing.
- Cue order must follow the referenced drawing actions' order.
- `write_equation` actions require `mode="equation"`.
- `write_text` actions accept `mode="handwrite"` or `mode="code"`; they reject
  `equation`.
- `reveal_duration_seconds` is an optional animation estimate in
  `[0.1, 600.0]`.

### 7.4 Highlight cues

- `HighlightPlan.cues` has 0–80 cues.
- Each cue targets an existing, non-`erase` drawing action.
- `order` values are unique and strictly increasing.
- The cue's `segment_id` must exist in the script and must not precede the
  target action's narration segment.
- Styles are `marker`, `underline`, `circle`, and `box`.
- `color` is a free-form string, max 32 chars; `duration_seconds` is an
  animation estimate in `[0.1, 600.0]`.

### 7.5 Continuity

`ContinuityPlan.mode` is one of:

- `fresh_board`: start clean; no carried actions or cleared regions.
- `continue_board`: carry at least one action ID from the previous plan.
- `clear_regions`: clear at least one region ID from the previous plan.

Carried action IDs and cleared region IDs must exist in the previous plan. An
erase action cannot be carried. An action cannot be carried from a region that
is also cleared. The previous plan must be available to the renderer when
continuity is not `fresh_board`.

### 7.6 Renderer ownership

The renderer owns font metrics, text wrapping, stroke animation, canvas
scaling, aspect ratio, coordinate origin, z-order, color interpretation,
accessibility adaptations, and the visual meaning of `draw_circle` /
`draw_rectangle` point pairs. The whiteboard plan does not contain external
images, video assets, provider prompts, or any instructor/avatar representation.

---

## 8. Narration timing contract for TTS integration

- `segment_id` values are stable identifiers. TTS/alignment must preserve them
  and the script's segment order.
- `estimated_duration_seconds` on each `NarrationSegment` is a planning estimate.
  It is not audio truth and must not be presented as actual TTS timing.
- `SceneScript.estimated_duration_seconds` is the sum of segment estimates.
  Duration-deviation and pacing checks are warnings only.
- Whiteboard actions and highlight cues anchor to `segment_id`; they contain no
  absolute timestamps. The Whiteboard Director never allocates audio time.
- `DrawingAction.duration_seconds`, `HandwritingCue.reveal_duration_seconds`, and
  `HighlightCue.duration_seconds` are animation/reveal estimates, not narration
  timings.
- TTS/alignment should return real per-segment start/end/duration keyed by
  `segment_id`. If a TTS component splits or merges segments, it must define and
  return an explicit mapping so whiteboard anchors can be re-resolved. That
  mapping is a downstream interface decision.
- Who enforces the target duration, tolerance, and re-planning policy remains a
  team decision. This component only estimates and warns.

---

## 9. Validation behavior and typed failures

Validation is layered:

1. **Structural contract validation** in constructors and `from_dict`:
   required/unknown fields, enums, exact ID format, text/collection limits,
   duplicate IDs, ordering, coordinate and region bounds, point-in-region
   geometry, source subset rules, highlight/continuity cross-references, and
   schema versions.
2. **Semantic validation** in `agents/scene_planning/validation.py`:
   - scene identity consistency across input, script, and whiteboard;
   - source IDs restricted to the scene's excerpts;
   - script duration deviation and speech-pace outliers (warnings);
   - configured-language mismatch (warning);
   - drawing actions and highlight cues anchored to real narration segments;
   - handwriting cues target write actions with compatible modes;
   - highlights target existing, non-erase actions and do not precede them;
   - continuity consistency with the previous plan, including erase/cleared
     region conflicts;
   - overlapping board regions, unanchored narration segments, excessive action
     counts, and long written text (warnings).
3. **Agent boundary validation**:
   - invalid upstream input raises `AgentInputError`;
   - provider call failures raise `LLMProviderError`;
   - malformed JSON or non-object JSON raises `AgentOutputError` with
     `.raw_output`;
   - contract or semantic output failures raise `AgentOutputError` with
     `.details` and, when available, `.raw_output`.

Typed failures:

```python
ScenePlanningError(message, *, details=())
AgentInputError(...)
LLMProviderError(...)
LLMResponseFormatError(...)
AgentOutputError(agent_name, message, *, details=(), raw_output=None)
```

`parse_json_object` tolerates exactly one complete Markdown JSON code fence. It
rejects incomplete/unclosed fences, trailing prose, duplicate object keys,
non-finite numbers, empty content, and non-object JSON.

The agents do **not** retry. Invalid provider output is never converted into a
fake successful result. Bounded retries, if desired, belong to orchestration and
must reuse the same typed failures.

Warnings do not block agent output. The team still needs to decide whether a
higher-level validation gate treats any warning as blocking, and who owns
factual/semantic review.

---

## 10. Integration points

### Upstream: planning and personalization

The provisional adapters are `LessonPlan`, `SceneBrief`,
`PersonalizationBrief`, `SourceExcerpt`, `SourceReference`, and `PageRange`.
They are intentionally smaller than a full planning implementation.

The planning/personalization owners need to confirm or replace:

- how a `SceneBrief` is produced and whether all fields are available;
- whether audience level is an enum (`beginner`/`intermediate`/`advanced`) or a
  richer scale;
- whether `language` is a BCP-47 tag or a display name;
- whether `sequence` is contiguous from 1 and how it relates to scene ordering;
- how source excerpts are selected per scene and whether multiple documents can
  appear in one lesson;
- whether selected pages may contain gaps (the current contract supports
  multiple ranges).

If planning already has equivalent models, this component should be adapted to
them rather than maintaining a parallel schema.

### LLM boundary

Both agents depend only on the local protocol shown in section 3.1.
`integrations/llm_provider.py` remains the provider boundary; a future adapter
should implement this protocol (or the team should agree on one shared protocol
and update this component).

The provider adapter is responsible for requesting JSON output when supported
and for mapping provider errors into typed failures. The agents tolerate a
single Markdown JSON code fence but reject prose or non-object JSON.

### Downstream: packaging, narration/audio, rendering, assembly

- **Scene Packager:** consume `ScenePlanningResult`; combine `script` and
  `whiteboard` with Visual Design Agent output and provider constraints. Do not
  ask this component to produce provider prompts or media.
- **Narration/TTS:** preserve `segment_id` values and segment order. Attach real
  audio timing downstream per segment. Estimated durations are planning hints,
  not rendered truth.
- **Renderer:** consume normalized coordinates and primitives. The renderer owns
  font metrics, stroke animation, canvas scaling, colors, z-order, and
  accessibility adaptations. Continuity actions from the previous plan must be
  available when rendering the next scene.
- **Video generation/validation:** validate synchronized audio/video against the
  script and whiteboard plan. This component's validation only checks planning
  contracts.
- **Assembly:** preserve `scene_id`, `sequence`, source references, and the
  continuity chain across scenes.
- **Orchestration:** call the Script Agent first, then the Whiteboard Director
  Agent with the resulting script and the previous scene's plan. Only the first
  scene should pass `previous_plan=None`.

### Visual Design Agent boundary

The Visual Design Agent is separate and remains unimplemented by this
component. Whiteboard direction covers board layout and drawing primitives. The
team must agree how visual-design assets/examples are merged with whiteboard
regions at packaging time, and which component owns z-order and image
placement.

---

## 11. Continuity rules

- No previous plan: the plan must use `fresh_board` with no carried actions or
  cleared regions.
- With a previous plan: `continue_board` must carry at least one action ID that
  exists in the previous plan; `clear_regions` must name at least one region that
  exists in the previous layout; any carried/cleared IDs are validated against
  that previous plan.
- An erase action cannot be carried forward because it leaves no persistent
  element.
- An action cannot be carried from a region that is also cleared.
- `fresh_board` is also valid when a previous plan exists if the new scene
  intentionally starts clean.
- The agent does not assume boards are cleared automatically. Renderers must
  honor the continuity mode, carry-over list, and clear-region list.

---

## 12. Known limitations

- **No factual/semantic verification.** Validation grounds citations to allowed
  source IDs; it cannot prove that narration faithfully represents the source.
  Human or future semantic review is required for factual accuracy.
- **No end-to-end video test.** Only unit tests with a deterministic fake LLM
  have run. No provider, TTS, audio, renderer, packaging, or video generation
  path has been exercised.
- **No provider adapter.** The local `LLMClient` protocol is a boundary, not an
  implementation. API keys, retries, rate limits, and provider error mapping are
  not implemented here.
- **No orchestration or retries.** The agents make one call and raise typed
  errors. Bounded retries and persistence are orchestration-owned.
- **Selected-page ranges are not duplicated in `ScenePlanningResult`.** The
  result preserves the scene's excerpts and page references, but packaging that
  needs the original selection must receive it from planning.
- **Some plan-wide rules are runtime-only.** JSON Schema cannot express unique
  action orders, cross-object references, point-in-region geometry, or
  highlight/continuity semantics. A schema-valid provider response can still
  fail runtime validation and become `AgentOutputError`.
- **Renderer semantics are provisional.** Coordinate origin, aspect ratio,
  circle/rectangle point interpretation, z-order, and highlight rendering are
  not fixed.
- **Visual Design integration is unresolved.** External assets, image
  placement, and merging with board regions are out of scope.
- **Dataclasses, not Pydantic.** The implementation is dependency-free. There is
  no concrete integration requirement to migrate at this time.
- **Source ingestion is provisional.** The canonical excerpt shape, multi-document
  behavior, and figure/equation/code/table representation need ingestion
  agreement.
- **No human review or citation display.** On-screen citation policy and
  review workflow are unresolved.

---

## 13. Decisions requiring team agreement

The repository had 13 unresolved coordination points, although the original
task referred to "ten". All 13 are classified below. None of them can be fully
resolved inside this component without another owner; the component currently
implements a documented provisional behavior for each. The handoff guide contains
the fillable owner checklist and confirmation questions in section 7 of
[`scene_planning_handoff.md`](scene_planning_handoff.md).

| # | Decision | Classification | Owner / needed decision | Current provisional contract |
| --- | --- | --- | --- | --- |
| 1 | Provisional upstream models (`LessonPlan`, `SceneBrief`, `PersonalizationBrief`, `SourceExcerpt`, `PageRange`) | Requires agreement from another teammate | Planning, personalization, ingestion: confirm or replace field names/shapes | Adapters in `models/lesson_plan.py`, `models/source.py` |
| 2 | Dataclasses versus Pydantic | Not currently blocking local tests | Team-wide: standardize or keep mixed boundaries | Frozen dependency-free dataclasses with strict `from_dict` |
| 3 | Source ingestion contract | Requires agreement from another teammate | Ingestion/planning: canonical excerpt shape, stable IDs, multi-document support, page numbering, figure/equation/code/table representation | `SourceKind` and `SourceExcerpt` are provisional |
| 4 | Provider interface | Requires a downstream interface definition | Provider/orchestration: implement `LLMClient` or define a shared protocol; structured output and error mapping | Local `LLMClient` protocol, `LLMRequest`/`LLMResponse` |
| 5 | Segment-level audio alignment | Requires a downstream interface definition | TTS/timing: preserve `segment_id`/order and return per-segment real timing; define split/merge mapping | Segment IDs are the only timing anchors; no absolute timestamps |
| 6 | Duration responsibility | Requires agreement from another teammate | Planning/TTS/orchestration: who enforces target duration, tolerance, and re-planning | Component estimates durations and emits warnings only |
| 7 | Visual Design integration | Requires agreement from another teammate | Visual Design/packaging: how assets merge with board regions, z-order, image placement | Whiteboard plan contains primitives only |
| 8 | Canonical scene contract | Requires a downstream interface definition | Scene Packager: define mapping from `ScenePlanningResult` to `video/scene_contract.py` and provider package | `ScenePlanningResult` is the recommended handoff, not the provider package |
| 9 | Renderer semantics | Requires a downstream interface definition | Renderer: coordinate origin, aspect ratio, font/stroke defaults, highlight behavior, `clear_regions` representation | Normalized `0.0`–`1.0` coordinates and named primitives only |
| 10 | Validation policy | Requires agreement from another teammate | Orchestration/review: which warnings block packaging; who owns factual/semantic review | Warnings are advisory; errors block agent output |
| 11 | Scene and lesson ID format | Requires agreement from another teammate | Planning/database/API: confirm compatibility with `[A-Za-z0-9][A-Za-z0-9._:-]{0,127}` | Conservative exact-match ID pattern |
| 12 | Versioning and compatibility | Not currently blocking local tests | All consumers: record schema versions and decide fail-versus-migrate for unknown versions | Unknown versions are rejected; versions are 1.0 |
| 13 | Human review and citation display | Requires agreement from another teammate | Product/review/UI: whether citations appear on-screen and how `SourceReference` is rendered without internal paths | References are preserved; display policy is unimplemented |

Items resolved within this component during the hardening pass (not team
decisions): exact text/ID preservation, schema/runtime limit alignment,
source-subset checks in `ScenePlanningResult`, highlight target/order rules,
continuity erase/cleared-region rules, point-in-region geometry, and strict JSON
parsing.

---

## 14. Tests and verification

Run from the repository root:

```bash
pytest -q
PYTHONPATH=src pytest -q
PYTHONPATH=src python3 -m compileall -q src tests
git diff --check
```

The unit tests use a deterministic fake LLM and require no credentials or
network access. They cover:

- page-range, source-reference, and selected-page validation;
- exact text/equation/code/source excerpt round-trips;
- ID format, duplicate IDs, and collection limits;
- scene identity in both agents and strictly increasing lesson scene order;
- script/whiteboard round-trips and cross-reference rejection;
- source-subset checks in the combined result;
- Script Agent success, unknown sources, scene mismatch, malformed JSON,
  duplicate JSON keys, incomplete fences, contract failures, provider failures,
  fenced JSON, exact text, and prompt contents;
- Whiteboard Director success, unknown segments, non-write handwriting cues,
  unknown/erase highlight targets, highlight-before-target rejection, geometry,
  handwriting order, action-type payload rules, continuity with/without a
  previous plan, invalid carry-over, erase/cleared-region conflicts, provider
  failures, and prompt contents;
- JSON Schema structure, serializability, representative payload validation, and
  runtime/schema limit agreement;
- segment-ID timing anchors, advisory duration estimates, and absence of
  absolute timestamps;
- combined `ScenePlanningResult` source preservation and scene identity;
- the runnable `examples/scene_planning_integration.py` flow with the deterministic
  fake LLM, exact result round-trip, and separate selected-page handoff.

Integration and end-to-end tests remain placeholders for the team components
that own orchestration, providers, rendering, and assembly.
