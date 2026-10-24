"""Unit test for the Script Agent to Whiteboard Director handoff.

Responsibility: verify that the two implemented agents can run sequentially with
one provider client and produce the combined downstream handoff artifact.
Workflow: deterministic fake-LLM flow test; no network or credentials.
Inputs: fixtures from ``tests/conftest.py``.
Outputs: pytest assertions only.
Interacts with: ``ScriptAgent``, ``WhiteboardDirectorAgent``, and
``ScenePlanningResult``.
"""

from __future__ import annotations

import json

from ai_video_generator.agents.scripting import ScriptAgent
from ai_video_generator.agents.whiteboard import WhiteboardDirectorAgent
from ai_video_generator.models.scene_planning import ScenePlanningResult


def test_agents_produce_source_preserving_scene_planning_result(
    planning_input, script_payload, whiteboard_payload, fake_llm_factory
):
    llm = fake_llm_factory(json.dumps(script_payload), json.dumps(whiteboard_payload))

    script = ScriptAgent(llm).generate(planning_input)
    whiteboard = WhiteboardDirectorAgent(llm).generate_for_scene(
        planning_input, script
    )
    result = ScenePlanningResult(
        lesson_id=planning_input.lesson_id,
        scene=planning_input.scene,
        script=script,
        whiteboard=whiteboard,
    )

    assert len(llm.requests) == 2
    assert result.scene == planning_input.scene
    assert result.script == script
    assert result.whiteboard == whiteboard
    assert result.scene.source_excerpts[0].page_number == 12
    assert result.scene.source_excerpts[1].page_number == 23
    assert set(result.whiteboard.segment_anchors) == {
        segment.segment_id for segment in result.script.segments
    }
    assert ScenePlanningResult.from_dict(result.to_dict()) == result
