"""Safe storage-relative path helpers shared by contracts and storage."""

from __future__ import annotations

import os
import re
from pathlib import Path

from ai_video_generator.core.exceptions import StoragePathError

_ARTIFACT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def normalize_artifact_key(value: str) -> str:
    """Normalize and validate a storage-relative POSIX artifact key."""

    key = str(value).strip().replace("\\", "/")
    if not key:
        raise ValueError("Artifact key must not be empty")
    if key.startswith("/") or re.match(r"^[A-Za-z]:", key):
        raise ValueError("Artifact key must be relative to storage")
    parts = key.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Artifact key must not contain empty, '.' or '..' segments")
    if not _ARTIFACT_KEY_RE.fullmatch(key):
        raise ValueError("Artifact key contains unsupported characters")
    return key


def resolve_within(root: Path, key: str) -> Path:
    """Resolve a key under ``root``, rejecting traversal and symlink escapes."""

    try:
        normalized = normalize_artifact_key(key)
    except ValueError as exc:
        raise StoragePathError(
            f"Unsafe storage key {key!r}: {exc}",
            details={"key": key},
            cause=exc,
        ) from exc

    root_resolved = root.resolve()
    candidate = (root_resolved / normalized).resolve(strict=False)
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        # Windows path comparisons are case-insensitive; commonpath is a second
        # check for platforms/paths where relative_to behaves unexpectedly.
        root_norm = os.path.normcase(str(root_resolved))
        candidate_norm = os.path.normcase(str(candidate))
        try:
            common = os.path.commonpath([root_norm, candidate_norm])
        except ValueError:
            common = ""
        if common != root_norm:
            raise StoragePathError(
                f"Storage key escapes the storage root: {key!r}",
                details={"key": key, "root": str(root_resolved)},
                cause=exc,
            ) from exc
    return candidate
