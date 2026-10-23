"""Workflow orchestrator.

Responsibility: coordinate the end-to-end workflow from document input to final
video, tracking stage state, dependencies, retries, and cancellation.
Workflow: central agent orchestration; called by application services.
Inputs: a project, preferences, and available source content (future).
Outputs: stage results, job progress, and final artifact references (future).
Interacts with: task router, specialized agents, models, and services.
No orchestration logic is implemented yet.
"""
