"""Whiteboard director agent.

Responsibility: translate a scene's explanation into a whiteboard plan with
progressive writing, diagrams, code blocks, equations, markers, arrows, and
highlights, while preserving continuity between scenes.
Workflow: scene planning, after script and visual intent are known.
Inputs: scene script, key points, source visuals, and personalization guidance (future).
Outputs: ordered drawing and reveal instructions for the scene (future).
Interacts with: script agent, visual design agent, whiteboard package, and packager.
No agent implementation or prompt is included yet. No instructor avatar is planned.
"""
