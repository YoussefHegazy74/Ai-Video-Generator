"""Atomic JSON artifact registry.

The registry is deliberately simple and single-process. It records provenance for
local artifacts; it is not a distributed database. Concurrent writers from
multiple processes are not supported.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ai_video_generator.core.constants import ArtifactKind
from ai_video_generator.core.exceptions import StorageError
from ai_video_generator.video.scene_contract import utc_now


class ArtifactRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    kind: ArtifactKind
    key: str
    sha256: str
    size_bytes: int = Field(ge=0)
    project_id: str | None = None
    job_id: str | None = None
    scene_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ArtifactRegistry:
    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self._lock = threading.RLock()
        self._records: dict[str, ArtifactRecord] | None = None

    def _load(self) -> dict[str, ArtifactRecord]:
        if self._records is not None:
            return self._records
        if not self.path.exists():
            self._records = {}
            return self._records
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            raw_records = payload.get("artifacts", [])
            self._records = {
                item["key"]: ArtifactRecord.model_validate(item)
                for item in raw_records
            }
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            raise StorageError(
                f"Artifact registry is unreadable: {self.path}",
                details={"path": str(self.path)},
                cause=exc,
            ) from exc
        return self._records

    def _flush(self) -> None:
        records = self._load()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "1.0",
            "artifacts": [
                record.model_dump(mode="json")
                for record in sorted(records.values(), key=lambda item: item.key)
            ],
        }
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        except OSError as exc:
            raise StorageError(
                f"Failed to write artifact registry: {self.path}",
                details={"path": str(self.path)},
                cause=exc,
            ) from exc
        finally:
            temporary.unlink(missing_ok=True)

    def register(self, record: ArtifactRecord) -> ArtifactRecord:
        with self._lock:
            records = self._load()
            records[record.key] = record
            self._flush()
        return record

    def find(self, key: str) -> ArtifactRecord | None:
        with self._lock:
            return self._load().get(key)

    def for_job(self, job_id: str) -> list[ArtifactRecord]:
        with self._lock:
            return sorted(
                (record for record in self._load().values() if record.job_id == job_id),
                key=lambda item: item.created_at,
            )

    def all(self) -> list[ArtifactRecord]:
        with self._lock:
            return sorted(self._load().values(), key=lambda item: item.key)
