"""Artifact registry.

Responsibility: track generated artifacts, their kinds, locations, provenance,
and relationships to projects, scenes, and jobs.
Workflow: storage layer used by ingestion, generation, validation, and assembly.
Inputs: artifact metadata and lifecycle events (future).
Outputs: artifact records and lookup results (future).
Interacts with: storage service, file storage, models, and API video schemas.
No registry implementation or persistence choice is defined yet.
"""
