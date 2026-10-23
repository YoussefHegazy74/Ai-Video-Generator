"""Shared agent contract.

Responsibility: define the common expectations for agent inputs, structured
outputs, retries, tracing, and failure reporting.
Workflow: inherited conceptually by all specialized agents and used by orchestration.
Inputs: task payloads and execution context (future).
Outputs: validated results or typed failures (future).
Interacts with: orchestration, task router, LLM provider, and logging.
No base class or execution logic is defined yet.
"""
