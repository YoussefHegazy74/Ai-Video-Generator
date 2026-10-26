"""Video generation and retrieval routes.

Responsibility: start generation jobs, report progress, and expose final outputs.
Workflow: HTTP API layer; delegates to scene generation and assembly services.
Inputs: project identifiers and generation options (future).
Outputs: job status, scene status, and video artifact metadata (future).
Interacts with: API schemas, dependencies, services, and artifact registry.
No endpoints are implemented yet.
"""
