"""Video model provider interface.

Responsibility: describe provider capabilities and submit scene video requests
without assuming a single supported duration, resolution, or audio behavior.
Workflow: infrastructure adapter used by the generation agent and scene service.
Inputs: packaged scene requests and provider options (future).
Outputs: generated clips, capability metadata, and provider errors (future).
Interacts with: scene packaging, generation agent, validation, and settings.
No interface or provider adapter is implemented yet; no provider is selected.
"""
