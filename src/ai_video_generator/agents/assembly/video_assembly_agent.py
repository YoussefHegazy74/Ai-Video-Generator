"""Video assembly agent.

Responsibility: join validated scenes in order, apply transitions, titles, and
intro/outro material, keep audio levels consistent, and attach final metadata.
Workflow: final stage after all scenes pass validation.
Inputs: ordered validated scene artifacts and assembly options (future).
Outputs: the assembled long-form video and output metadata (future).
Interacts with: validation agent, video assembly helpers, storage, and artifact registry.
No agent implementation or assembly logic is included yet.
"""
