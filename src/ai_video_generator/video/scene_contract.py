"""Canonical scene contract.

Responsibility: define the shared, provider-neutral shape of a scene as it moves
from planning to packaging, generation, validation, and assembly.
Workflow: boundary contract for the video pipeline.
Inputs: scene plans from the planning and packaging stages (future).
Outputs: validated scene objects for generation and validation (future).
Interacts with: models, scene packager, video model provider, and validation.
No schema, versioning, or serialization logic is defined yet.
"""
