"""Artifact-level storage use cases and provenance registration."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ai_video_generator.core.constants import ArtifactKind
from ai_video_generator.core.exceptions import StorageError
from ai_video_generator.core.logging import get_logger
from ai_video_generator.integrations.storage_provider import StorageProvider
from ai_video_generator.storage.artifact_registry import (
    ArtifactRecord,
    ArtifactRegistry,
)
from ai_video_generator.utils.identifiers import new_id, safe_component
from ai_video_generator.video.media_probe import file_sha256

logger = get_logger(__name__)
_SECRET_MARKERS = ("api_key", "apikey", "authorization", "password", "secret", "token")


def _safe_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        if any(marker in key.lower() for marker in _SECRET_MARKERS):
            continue
        if isinstance(value, dict):
            safe[key] = _safe_metadata(value)
        else:
            safe[key] = value
    return safe


class ArtifactStorageService:
    def __init__(self, storage: StorageProvider, registry: ArtifactRegistry) -> None:
        self.storage = storage
        self.registry = registry

    @staticmethod
    def job_prefix(job_id: str) -> str:
        return f"jobs/{safe_component(job_id, field='job_id')}"

    @classmethod
    def scene_prefix(cls, job_id: str, scene_index: int, scene_id: str) -> str:
        if scene_index < 0:
            raise ValueError("scene_index must be non-negative")
        safe_scene = safe_component(scene_id, field="scene_id")
        return f"{cls.job_prefix(job_id)}/scenes/{scene_index:03d}-{safe_scene}"

    def store_file(
        self,
        source: Path,
        key: str,
        kind: ArtifactKind,
        *,
        job_id: str | None = None,
        scene_id: str | None = None,
        project_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRecord:
        source = Path(source)
        if not source.is_file():
            raise StorageError(
                f"Cannot store missing file: {source}",
                details={"source": str(source)},
            )
        sha256 = file_sha256(source)
        size_bytes = source.stat().st_size
        stored_key = self.storage.put_file(source, key)
        record = ArtifactRecord(
            artifact_id=new_id("art"),
            kind=kind,
            key=stored_key,
            sha256=sha256,
            size_bytes=size_bytes,
            project_id=project_id,
            job_id=job_id,
            scene_id=scene_id,
            metadata=_safe_metadata(metadata or {}),
        )
        self.registry.register(record)
        return record

    def store_json(
        self,
        data: Any,
        key: str,
        kind: ArtifactKind,
        *,
        job_id: str | None = None,
        scene_id: str | None = None,
        project_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRecord:
        try:
            payload = json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise StorageError(
                f"Cannot serialize JSON artifact {key}",
                details={"key": key},
                cause=exc,
            ) from exc
        sha256 = hashlib.sha256(payload).hexdigest()
        stored_key = self.storage.put_bytes(payload, key)
        record = ArtifactRecord(
            artifact_id=new_id("art"),
            kind=kind,
            key=stored_key,
            sha256=sha256,
            size_bytes=len(payload),
            project_id=project_id,
            job_id=job_id,
            scene_id=scene_id,
            metadata=_safe_metadata(metadata or {}),
        )
        self.registry.register(record)
        return record

    def read_json(self, key: str) -> Any:
        return self.storage.read_json(key)

    def exists(self, key: str) -> bool:
        return self.storage.exists(key)

    def absolute_path(self, key: str) -> Path:
        return self.storage.absolute_path(key)

    def delete(self, key: str) -> None:
        self.storage.delete(key)

    def list_keys(self, prefix: str = "") -> list[str]:
        return self.storage.list_keys(prefix)

    def acquire_lock(self, key: str):  # type: ignore[no-untyped-def]
        return self.storage.acquire_lock(key)

    @contextmanager
    def temporary_directory(self, prefix: str) -> Iterator[Path]:
        path = self.storage.create_temp_dir(prefix)
        try:
            yield path
        finally:
            try:
                self.storage.cleanup_temp_dir(path)
            except Exception:
                logger.exception(
                    "Failed to clean up temporary directory",
                    extra={"temp_dir": str(path)},
                )
