"""Loader for uploaded books and documents.

Responsibility: accept an uploaded file, detect its format, and hand it to the
appropriate parser while preserving source metadata.
Workflow: document ingestion, first step after upload.
Inputs: a stored upload path or stream and source metadata (future).
Outputs: a loaded document handle and format details (future).
Interacts with: upload routes, storage, pdf parser, and page extractor.
No loading logic is implemented yet.
"""
