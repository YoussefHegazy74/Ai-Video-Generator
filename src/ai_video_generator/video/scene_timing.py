"""Scene timing and duration planning.

Responsibility: allocate the target video duration across scenes and reconcile
desired pacing with model-specific minimum and maximum scene durations.
Workflow: planning and packaging, reused by validation.
Inputs: lesson outline, target duration, and provider capabilities (future).
Outputs: per-scene duration targets and timing warnings (future).
Interacts with: planning agent, scene packager, validation, and assembly.
No timing logic is implemented yet; supported durations remain provider-specific.
"""
