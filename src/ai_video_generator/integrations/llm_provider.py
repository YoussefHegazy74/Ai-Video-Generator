"""LLM provider interface.

Responsibility: define a provider-agnostic boundary for model requests used by
planning, scripting, personalization, and validation agents.
Workflow: infrastructure adapter consumed by agents through a stable interface.
Inputs: structured prompts, context, and generation options (future).
Outputs: structured model responses, usage metadata, and errors (future).
Interacts with: base agent, specialized agents, core settings, and core exceptions.
No interface or provider adapter is implemented yet; no provider is selected.
"""
