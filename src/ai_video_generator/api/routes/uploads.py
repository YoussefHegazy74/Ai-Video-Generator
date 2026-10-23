"""Document upload and extraction routes.

Responsibility: accept books/PDFs, validate uploads, and request page extraction.
Workflow: HTTP API layer; delegates to the document service and ingestion modules.
Inputs: uploaded files, page range, and source metadata (future).
Outputs: upload identifiers and extraction status (future).
Interacts with: API schemas, dependencies, document service, and storage.
No endpoints are implemented yet.
"""
