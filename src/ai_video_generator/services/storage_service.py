"""Storage application service.

Responsibility: provide a consistent use-case-level API for saving, locating, and
removing project artifacts without exposing storage implementation details.
Workflow: used by ingestion, generation, validation, and assembly services.
Inputs: artifact content and metadata (future).
Outputs: stable artifact references and retrieval results (future).
Interacts with: storage package, storage provider, and artifact registry.
No service logic is implemented yet.
"""
