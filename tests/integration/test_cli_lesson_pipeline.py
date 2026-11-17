"""Smoke test for the full lesson CLI in plan-only mode.

Responsibility: verify the documented command wires configuration, packaging, and
the runtime adapter without requiring FFmpeg or an LLM provider.
Workflow: runs the script as a subprocess from the repository root.
Inputs: the committed demo lesson fixtures.
Outputs: a JSON plan summary with the expected lesson and scenes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_lesson_cli_plan_only_emits_a_runtime_scene_set():
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_lesson_pipeline.py",
            "--lesson-plan",
            "examples/demo_lesson/lesson_plan.json",
            "--planning-dir",
            "examples/demo_lesson/planning",
            "--plan-only",
            "--plain-logs",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    # Logging may precede the JSON summary on stdout; parse the final object.
    payload = json.loads(completed.stdout[completed.stdout.index("{") :])
    assert payload["status"] == "planned"
    assert payload["lesson_id"] == "lesson-fractions-demo"
    scene_set = payload["scene_set"]
    assert scene_set["project_id"] == "lesson-fractions-demo"
    assert [scene["scene_index"] for scene in scene_set["scenes"]] == [0, 1]
    assert [scene["scene_id"] for scene in scene_set["scenes"]] == [
        "fractions-intro",
        "fractions-addition",
    ]
    assert scene_set["scenes"][0]["source_references"][0]["page_numbers"] == [1]
