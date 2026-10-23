# AI Educational Video Generator

> **Status:** initial project skeleton only. The repository contains documentation
> placeholders, package layout, and configuration placeholders. No agents, API
> endpoints, generation pipeline, or working video features are implemented yet.

## Vision

Turn selected pages from a user-provided book or PDF into a personalized,
long-form educational video. The viewing experience resembles an online
screen-sharing lesson on a digital whiteboard: an instructor's voice explains the
material while handwriting, diagrams, code, equations, pointer movements,
highlights, and marker strokes appear progressively. The instructor's face and
body never appear.

Users will be able to choose:

- a book or PDF and a page range;
- an audience level and preferred language;
- a target duration, potentially around 30 minutes;
- a predefined teaching style (for example football, cooking, or gaming) or a
  custom style.

## Intended user workflow

1. Create a project and upload a book or PDF.
2. Select the page range to teach.
3. Configure audience level, language, target duration, and teaching style.
4. Review the generated lesson outline and scene plan (planned capability).
5. Start scene generation and validation.
6. Download the assembled long-form video and its metadata (planned capability).

## High-level pipeline

Document ingestion → personalization → lesson planning → scene and script
planning → whiteboard direction → scene packaging → synchronized audio/video
generation → scene validation → final assembly. Agent orchestration coordinates
the stages.

| Stage | Responsibility | Main location |
| --- | --- | --- |
| Ingestion | Load documents, extract pages, preserve structure | `src/ai_video_generator/ingestion/` |
| Personalization | Resolve audience, language, duration, and style guidance | `src/ai_video_generator/personalization/`, `src/ai_video_generator/agents/personalization/` |
| Planning | Build the lesson outline and scene breakdown | `src/ai_video_generator/agents/planning/`, `src/ai_video_generator/models/lesson_plan.py` |
| Scene planning | Write narration, plan visuals, direct the whiteboard | `src/ai_video_generator/agents/scripting/`, `src/ai_video_generator/agents/whiteboard/`, `src/ai_video_generator/agents/visual_design/` |
| Packaging | Combine script and visual plan into a provider-ready scene package | `src/ai_video_generator/agents/scene_packaging/`, `src/ai_video_generator/video/scene_contract.py` |
| Generation | Produce each scene with synchronized audio and video | `src/ai_video_generator/agents/generation/`, `src/ai_video_generator/integrations/` |
| Validation | Check integrity, duration, audio, dimensions, and continuity | `src/ai_video_generator/agents/validation/`, `src/ai_video_generator/video/` |
| Assembly | Join scenes, add transitions and titles, normalize output | `src/ai_video_generator/agents/assembly/`, `src/ai_video_generator/video/assembly.py` |

The original workflow diagram is preserved at
`AI Educational Video Generator Workflow.png`.

## Proposed source organization

```text
docs/                       Project and design documentation (not yet written)
config/                     Configuration placeholders
src/ai_video_generator/
  api/                      HTTP interface: routes, schemas, dependencies
  core/                     Settings, logging, exceptions, constants
  ingestion/                Document loading, parsing, page extraction, normalization
  agents/                   Orchestration and specialized workflow agents
  models/                   Domain models shared across layers
  services/                 Application use-case coordination
  integrations/             Provider interfaces for LLM, video, and storage
  whiteboard/               Board layout, drawing, handwriting, highlighting, continuity
  personalization/          Style catalog, custom styles, example mapping
  video/                    Scene contract, timing, validation, continuity, assembly
  storage/                  File storage and artifact registry
  utils/                    Small shared utilities
tests/                      Unit, integration, and end-to-end test placeholders
scripts/                    Future operational and development scripts
data/                       Local runtime data (uploads, extracted content, outputs)
artifacts/                  Generated videos, manifests, and reports
```

## Current limitations and pending decisions

- No dependency manifest or lockfile is included yet; Python packaging and
  third-party dependency choices are still pending.
- No Dockerfile or container orchestration file is included yet; the deployment
  model is undecided.
- Configuration files contain comments only and do not define values, model
  names, provider settings, or credentials.
- Service and integration modules are documentation placeholders. Their APIs
  will be designed during implementation.

## Documentation

Start with `docs/project_overview.md` and `docs/system_architecture.md`.
Workflow and contract details will be added as decisions are made.
