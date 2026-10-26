"""API schemas for generation jobs and video outputs.

Responsibility: define job submission payloads, progress payloads, and final
artifact metadata.
Workflow: HTTP API boundary for generation and video routes.
Inputs: job requests and domain job state (future).
Outputs: serialized job and artifact payloads (future).
Interacts with: video routes, generation services, and the artifact registry.
No schema classes are defined yet.
"""
