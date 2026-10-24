# Tests

**Status:** pytest-based unit tests exist for the scene-planning contracts,
Script Agent, and Whiteboard Director Agent. Integration and end-to-end suites
remain placeholders.

This directory holds automated tests for ingestion, agent workflows, scene
contracts, provider adapters, validation, and assembly. Run the current suite
from the repository root with:

```bash
PYTHONPATH=src pytest -q
```

## Intended contents

- `unit/`: isolated tests for pure logic and contracts.
- `integration/`: tests across modules with fake or recorded provider responses.
- `end_to_end/`: full workflow tests from a small document to an assembled video.
- Test fixtures, shared factories, and evaluation data (added later).
- Instructions for running the suite once tooling is selected.

Tests must not require real credentials or paid provider calls by default.
