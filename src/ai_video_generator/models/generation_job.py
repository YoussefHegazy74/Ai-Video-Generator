"""Generation job domain model.

Responsibility: track the lifecycle, progress, attempts, failures, and artifact
references of a video generation run.
Workflow: created when generation starts and updated by services and agents.
Inputs: job requests and stage results (future).
Outputs: job state consumed by API routes and orchestration (future).
Interacts with: project, scene, orchestration, services, and artifact registry.
No model class is defined yet.
"""
