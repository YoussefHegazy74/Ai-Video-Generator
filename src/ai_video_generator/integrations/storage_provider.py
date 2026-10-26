"""Storage provider contract.

Storage keys are relative POSIX paths. Implementations must reject absolute
paths, ``..`` traversal, and symlink escapes.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StorageProvider(Protocol):
    root: Path

    def put_file(self, source: Path, key: str, *, overwrite: bool = True) -> str:
        """Copy a local file into storage and return its normalized key."""

    def put_bytes(self, data: bytes, key: str, *, overwrite: bool = True) -> str:
        """Atomically store bytes and return the normalized key."""

    def read_bytes(self, key: str) -> bytes:
        """Read stored bytes or raise ``ArtifactNotFoundError``."""

    def write_json(self, data: Any, key: str, *, overwrite: bool = True) -> str:
        """Atomically serialize JSON to storage."""

    def read_json(self, key: str) -> Any:
        """Read and deserialize JSON from storage."""

    def exists(self, key: str) -> bool:
        """Return whether a regular file exists for the key."""

    def delete(self, key: str) -> None:
        """Delete a stored file; missing files are a no-op."""

    def absolute_path(self, key: str) -> Path:
        """Resolve a safe absolute path for local media tools."""

    def list_keys(self, prefix: str = "") -> list[str]:
        """List stored keys under a prefix."""

    def create_temp_dir(self, prefix: str) -> Path:
        """Create a job-scoped temporary directory."""

    def cleanup_temp_dir(self, path: Path) -> None:
        """Remove a directory previously returned by ``create_temp_dir``."""

    def acquire_lock(self, key: str) -> AbstractContextManager[None]:
        """Acquire a non-blocking exclusive generation lock."""
