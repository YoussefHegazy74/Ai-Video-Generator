"""Task router.

Responsibility: route workflow tasks to the correct agent, enforce task
dependencies and ordering, and expose a place for retry and failure policy.
Workflow: supports the workflow orchestrator.
Inputs: task descriptions, dependencies, and execution context (future).
Outputs: dispatched tasks and normalized agent results (future).
Interacts with: workflow orchestrator, base agent contract, and core exceptions.
No routing logic is implemented yet.
"""
