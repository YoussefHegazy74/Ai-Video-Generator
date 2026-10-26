# Scene Contract

**Status:** implemented, schema version `1.0`.
**Implementation:** `src/ai_video_generator/video/scene_contract.py`.
**Producer:** `ScenePackager` in
`src/ai_video_generator/agents/scene_packaging/scene_packager_agent.py`.
**Consumers:** provider-neutral scene prompt construction and downstream
generation, validation, and assembly components.

`ScenePackage` is the canonical, provider-neutral description of one planned
scene. It composes the existing script, visual, whiteboard, timing, and source
contracts by reference; it does not duplicate their content.

## Required fields

```python
ScenePackage(
    lesson_id: str,
    scene_id: str,
    script: SceneScript,
    visual: VisualPlan,
    whiteboard: WhiteboardPlan,
    timeline: SceneTimeline,
    source_excerpts: tuple[SourceExcerpt, ...],
    schema_version: str = "1.0",
)
```

All identifiers use the shared pattern
`[A-Za-z0-9][A-Za-z0-9._:-]{0,127}`. `source_excerpts` preserves the planning
order and must contain at least one `SourceExcerpt` with unique `source_id`
values. Visual items, script key points, whiteboard anchors, and timeline entries
remain in their original order.

## Validation invariants

- Only supported schema versions are accepted; unknown versions are rejected.
- `script`, `visual`, `whiteboard`, and `timeline` must all describe the same
  `scene_id` as the package.
- Timeline segment IDs and order must match the script's narration segments
  exactly, and each timeline duration must equal the corresponding
  `estimated_duration_seconds` within the timing tolerance.
- Every whiteboard segment anchor must reference an existing script
  `segment_id`.
- Every script key-point `source_id` must exist in `source_excerpts`.
- Every visual item `segment_id`, `key_point_id`, and `source_id` must reference
  the corresponding script or source content.
- Empty visual plans are valid. The nested contracts enforce their own required
  fields, collection limits, and semantic rules during construction.
- `from_dict` rejects missing and unknown fields. Round-tripping through
  `to_dict` / `from_dict` is stable.

## Timing semantics

`SceneTimeline` is an explicit **planning estimate** derived from each
narration segment's `estimated_duration_seconds`. It is not real TTS or audio
timing. `ScenePackager` applies explicitly supplied `DurationLimits`; duration
errors block package creation, while the advisory `timing.target_mismatch`
warning is retained on the packaged timeline. No narration is truncated,
stretched, rewritten, or removed.

## Ownership boundaries

- `VisualPlan` describes *what* teaching visual should be shown.
- `WhiteboardPlan` owns layout regions and drawing primitives.
- `ScenePackage` verifies identity, ordering, and references. It does not invent
  visual content, choose board layout, validate rendered media, or perform
  semantic fact-checking.

## Serialization and dependencies

All contracts are frozen, slots-based, dependency-free dataclasses with
`from_dict`, `to_dict`, and `to_json`. There is no Pydantic or runtime provider
dependency at this boundary.

## Out of scope

The package contains no provider prompts, model names, API fields, asset URLs,
storage details, renderer instructions, or actual audio timestamps. The original
`selected_pages` selection is not duplicated here; a consumer that needs it must
receive it separately from planning.
