"""Domain exception definitions.

Responsibility: define a clear exception hierarchy for validation, ingestion,
provider, and job failures.
Workflow: shared error vocabulary across services, agents, and the API.
Inputs: failure details from the component that raises an exception (future).
Outputs: typed exceptions and consistent error metadata (future).
Interacts with: API error handling, orchestration retries, and provider adapters.
No exception classes are defined yet.
"""
