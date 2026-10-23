"""Storage provider interface.

Responsibility: isolate local filesystem or object-storage details behind a
small read/write/delete contract.
Workflow: infrastructure adapter used by storage services and ingestion.
Inputs: artifact keys, content streams, and metadata (future).
Outputs: stored locations, retrieved content, and storage errors (future).
Interacts with: storage service, file storage, and artifact registry.
No interface or provider adapter is implemented yet.
"""
