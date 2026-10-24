"""Tests for the runnable scene-planning integration example.

Responsibility: verify that ``examples/scene_planning_integration.py`` exercises
the current public APIs, validates and serializes ``ScenePlanningResult``, and
keeps ``selected_pages`` as a separate packaging handoff.
Workflow: deterministic fake-LLM test plus one subprocess run of the example.
Inputs: fixtures from ``tests/conftest.py`` and the example module.
Outputs: pytest assertions only.
Interacts with: the example, both agents, and the shared contracts.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from ai_video_generator.models.scene_planning import ScenePlanningResult

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_PATH = ROOT / "examples" / "scene_planning_integration.py"


def _load_example() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "scene_planning_integration_example", EXAMPLE_PATH
    )
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load example from {EXAMPLE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


example = _load_example()


def test_example_runs_public_agents_and_keeps_selected_pages_separate(
    planning_input,
    script_payload,
    whiteboard_payload,
    fake_llm_factory,
):
    llm = fake_llm_factory(
        json.dumps(script_payload),
        json.dumps(whiteboard_payload),
    )

    result, selected_pages = example.run_scene_planning(planning_input, llm)

    assert len(llm.requests) == 2
    assert result.lesson_id == planning_input.lesson_id
    assert result.scene == planning_input.scene
    assert result.script.scene_id == planning_input.scene.scene_id
    assert result.whiteboard.scene_id == planning_input.scene.scene_id
    assert selected_pages == planning_input.selected_pages
    assert "selected_pages" not in result.to_dict()

    payload = example.build_packaging_payload(result, selected_pages)

    assert payload["selected_pages_for_packaging"] == [
        {"start_page": 10, "end_page": 20},
        {"start_page": 22, "end_page": 24},
    ]
    restored = ScenePlanningResult.from_dict(payload["scene_planning_result"])
    assert restored == result
    assert json.loads(json.dumps(payload)) == payload


def test_example_runs_as_a_script_from_the_repository_root():
    completed = subprocess.run(
        [sys.executable, str(EXAMPLE_PATH)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )

    payload = json.loads(completed.stdout)
    result = ScenePlanningResult.from_dict(payload["scene_planning_result"])

    assert result.lesson_id == "lesson-001"
    assert result.scene.scene_id == "scene-001"
    assert result.script.segments[0].segment_id == "seg-1"
    assert payload["selected_pages_for_packaging"] == [
        {"start_page": 10, "end_page": 20},
        {"start_page": 22, "end_page": 24},
    ]
    assert completed.stderr == ""
