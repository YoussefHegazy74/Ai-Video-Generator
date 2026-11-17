# Deterministic demo lesson

This directory contains **integration fixtures**, not a real lesson plan authored
by a user or a model.

`lesson_plan.json` and the per-scene `*.scene_planning.json` /
`*.visual_planning.json` files were produced by `examples/build_demo_lesson.py`,
which drives the real Script Agent, Whiteboard Director Agent, Example & Visual
Agent, and Scene Packager with a fixed fake LLM response queue. The purpose is to
exercise packaging, adaptation, generation, validation, and assembly
deterministically without an LLM provider or network access.

They are consumed by:

```bash
python scripts/run_lesson_pipeline.py \
  --lesson-plan examples/demo_lesson/lesson_plan.json \
  --planning-dir examples/demo_lesson/planning \
  --job-id demo
```

Regenerate all files (they are deterministic):

```bash
python examples/build_demo_lesson.py
```

Replacing these fixtures with real planner output is expected once document
ingestion, lesson planning, and a live `LLMClient` adapter are implemented.
