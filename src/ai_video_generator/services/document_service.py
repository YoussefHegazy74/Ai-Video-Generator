"""Document application service.

Responsibility: coordinate upload validation, document loading, page extraction,
normalization, and persistence of ingestion results.
Workflow: document ingestion use cases called by API routes or orchestration.
Inputs: upload references, page ranges, and source metadata (future).
Outputs: normalized document content and extraction artifacts (future).
Interacts with: ingestion modules, storage service, project model, and agents.
No service logic is implemented yet.
"""
