"""Local filesystem storage implementation.

Writes are atomic within the storage root, keys are validated against traversal,
and temporary directories are isolated from artifact keys.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ai_video_generator.core.exceptions import (
    ArtifactNotFoundError,
    DuplicateGenerationError,
    StorageError,
    StoragePathError,
)
from ai_video_generator.core.logging import get_logger
from ai_video_generator.utils.identifiers import safe_component
from ai_video_generator.utils.paths import normalize_artifact_key, resolve_within

logger = get_logger(__name__)


class LocalFileStorage:
    def __init__(self, root: Path, *, temp_root: Path | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        self.temp_root = (
            Path(temp_root).expanduser().resolve()
            if temp_root is not None
            else (self.root / ".work").resolve()
        )
        self.root.mkdir(parents=True, exist_ok=True)
        self.temp_root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return resolve_within(self.root, key)

    @staticmethod
    def _normalized_key(key: str) -> str:
        try:
            return normalize_artifact_key(key)
        except ValueError as exc:
            raise StoragePathError(
                f"Unsafe storage key {key!r}: {exc}",
                details={"key": key},
                cause=exc,
            ) from exc

    def put_file(self, source: Path, key: str, *, overwrite: bool = True) -> str:
        source = Path(source)
        if not source.is_file():
            raise ArtifactNotFoundError(
                f"Source file does not exist: {source}",
                details={"source": str(source)},
            )
        normalized = self._normalized_key(key)
        destination = self._path(normalized)
        if destination.exists() and not overwrite:
            raise StorageError(
                f"Artifact already exists: {normalized}",
                details={"key": normalized},
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f".{destination.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, destination)
        except OSError as exc:
            raise StorageError(
                f"Failed to store artifact {normalized}",
                details={"key": normalized},
                cause=exc,
            ) from exc
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)
        return normalized

    def put_bytes(self, data: bytes, key: str, *, overwrite: bool = True) -> str:
        normalized = self._normalized_key(key)
        destination = self._path(normalized)
        if destination.exists() and not overwrite:
            raise StorageError(
                f"Artifact already exists: {normalized}",
                details={"key": normalized},
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f".{destination.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        except OSError as exc:
            raise StorageError(
                f"Failed to store artifact {normalized}",
                details={"key": normalized},
                cause=exc,
            ) from exc
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)
        return normalized

    def read_bytes(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise ArtifactNotFoundError(
                f"Artifact not found: {key}",
                details={"key": key},
            )
        try:
            return path.read_bytes()
        except OSError as exc:
            raise StorageError(
                f"Failed to read artifact {key}",
                details={"key": key},
                cause=exc,
            ) from exc

    def write_json(self, data: Any, key: str, *, overwrite: bool = True) -> str:
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
                f"Data for {key} is not JSON serializable",
                details={"key": key},
                cause=exc,
            ) from exc
        return self.put_bytes(payload, key, overwrite=overwrite)

    def read_json(self, key: str) -> Any:
        raw = self.read_bytes(key)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StorageError(
                f"Artifact is not valid JSON: {key}",
                details={"key": key},
                cause=exc,
            ) from exc

    def exists(self, key: str) -> bool:
        try:
            return self._path(key).is_file()
        except StoragePathError:
            return False

    def delete(self, key: str) -> None:
        path = self._path(key)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError(
                f"Failed to delete artifact {key}",
                details={"key": key},
                cause=exc,
            ) from exc

    def absolute_path(self, key: str) -> Path:
        return self._path(key)

    def list_keys(self, prefix: str = "") -> list[str]:
        prefix = prefix.replace("\\", "/").strip("/")
        if prefix:
            try:
                normalize_artifact_key(prefix)
            except ValueError as exc:
                raise StoragePathError(
                    f"Invalid key prefix {prefix!r}",
                    details={"prefix": prefix},
                    cause=exc,
                ) from exc
        keys: list[str] = []
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(self.root).as_posix()
            if relative.startswith(".work/"):
                continue
            if prefix and not relative.startswith(prefix):
                continue
            keys.append(relative)
        return sorted(keys)

    def create_temp_dir(self, prefix: str) -> Path:
        safe_component(prefix, field="temporary directory prefix")
        self.temp_root.mkdir(parents=True, exist_ok=True)
        path = Path(tempfile.mkdtemp(prefix=f"{prefix}-", dir=self.temp_root))
        return path.resolve()

    def cleanup_temp_dir(self, path: Path) -> None:
        resolved = Path(path).resolve()
        temp_root = self.temp_root.resolve()
        try:
            resolved.relative_to(temp_root)
        except ValueError as exc:
            raise StoragePathError(
                "Refusing to remove a directory outside the temporary root",
                details={"path": str(resolved), "temp_root": str(temp_root)},
                cause=exc,
            ) from exc
        if resolved == temp_root:
            raise StoragePathError("Refusing to remove the temporary root itself")
        if resolved.exists():
            shutil.rmtree(resolved)

    @contextmanager
    def acquire_lock(self, key: str) -> Iterator[None]:
        lock_path = self._path(key)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        try:
            descriptor = os.open(
                lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError as exc:
            raise DuplicateGenerationError(
                f"Generation lock already exists: {key}. Another request may be "
                "generating this scene. If the process crashed, remove the lock "
                "only after verifying no generation is running.",
                details={"lock_key": key},
                cause=exc,
            ) from exc
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(token)
            yield
        finally:
            try:
                if lock_path.is_file():
                    current = lock_path.read_text(encoding="utf-8").strip()
                    if current == token:
                        lock_path.unlink()
                    else:
                        logger.warning(
                            "Generation lock ownership changed; not deleting",
                            extra={"lock_key": key},
                        )
            except OSError:
                logger.exception(
                    "Failed to clean up generation lock",
                    extra={"lock_key": key},
                )
