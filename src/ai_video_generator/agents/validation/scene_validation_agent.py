"""Scene validation agent.

Responsibility: verify generated scene integrity, expected duration, audio
presence, visual dimensions, scene order, and visual continuity; reserve checks
for written text and code accuracy.
Workflow: quality gate after each scene is generated and before assembly.
Inputs: scene package, generated media, and validation criteria (future).
Outputs: pass/fail results, diagnostics, and retry recommendations (future).
Interacts with: generation agent, audio/video validation helpers, and assembly.
No agent implementation or validation logic is included yet.
"""
