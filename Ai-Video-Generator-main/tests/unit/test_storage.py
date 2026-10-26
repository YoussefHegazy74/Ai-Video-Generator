from __future__ import annotations

from pathlib import Path

import pytest

from ai_video_generator.core.constants import ArtifactKind
from ai_video_generator.core.exceptions import (
    DuplicateGenerationError,
    StorageError,
    StoragePathError,
)
from ai_video_generator.services.storage_service import ArtifactStorageService
from ai_video_generator.storage.file_storage import LocalFileStorage


def test_file_storage_round_trip_and_listing(file_storage: LocalFileStorage) -> None:
    file_storage.put_bytes(b"hello", "jobs/job-1/report.txt")
    file_storage.write_json({"ok": True}, "jobs/job-1/data.json")

    assert file_storage.read_bytes("jobs/job-1/report.txt") == b"hello"
    assert file_storage.read_json("jobs/job-1/data.json") == {"ok": True}
    assert file_storage.exists("jobs/job-1/report.txt")
    assert file_storage.list_keys("jobs/job-1") == [
        "jobs/job-1/data.json",
        "jobs/job-1/report.txt",
    ]


def test_file_storage_rejects_traversal_and_absolute_keys(
    file_storage: LocalFileStorage,
) -> None:
    with pytest.raises(StoragePathError):
        file_storage.put_bytes(b"x", "../escape.txt")
    with pytest.raises(StoragePathError):
        file_storage.put_bytes(b"x", "C:/escape.txt")
    with pytest.raises(StoragePathError):
        file_storage.read_bytes("jobs/../../escape.txt")
    with pytest.raises(StoragePathError):
        file_storage.absolute_path("..\\escape.txt")
    assert file_storage.exists("../escape.txt") is False


def test_generation_lock_is_exclusive_and_released(
    file_storage: LocalFileStorage,
) -> None:
    key = "jobs/job-1/locks/000-scene.lock"
    with file_storage.acquire_lock(key):
        with pytest.raises(DuplicateGenerationError):
            with file_storage.acquire_lock(key):
                pass
        assert file_storage.exists(key)
    assert not file_storage.exists(key)

    with file_storage.acquire_lock(key):
        pass


def test_temporary_directory_is_isolated_and_cleaned(
    file_storage: LocalFileStorage, tmp_path: Path
) -> None:
    temp = file_storage.create_temp_dir("test")
    assert temp.is_dir()
    (temp / "frame.bin").write_bytes(b"x")
    assert not any("test-" in key for key in file_storage.list_keys())
    file_storage.cleanup_temp_dir(temp)
    assert not temp.exists()

    with pytest.raises(StoragePathError):
        file_storage.cleanup_temp_dir(tmp_path)


def test_artifact_service_registers_provenance_and_redacts_metadata(
    artifact_storage: ArtifactStorageService,
) -> None:
    record = artifact_storage.store_json(
        {"value": 1},
        "jobs/job-1/report.json",
        ArtifactKind.JOB_MANIFEST,
        job_id="job-1",
        project_id="project-1",
        metadata={"api_key": "must-not-be-stored", "safe": "visible"},
    )

    assert record.key == "jobs/job-1/report.json"
    assert record.sha256
    assert record.metadata == {"safe": "visible"}
    assert artifact_storage.registry.find(record.key) is not None
    assert [item.key for item in artifact_storage.registry.for_job("job-1")] == [
        record.key
    ]
    assert artifact_storage.read_json(record.key) == {"value": 1}


def test_artifact_service_requires_an_existing_source(
    artifact_storage: ArtifactStorageService, tmp_path: Path
) -> None:
    with pytest.raises(StorageError):
        artifact_storage.store_file(
            tmp_path / "missing.mp4",
            "jobs/job-1/video.mp4",
            ArtifactKind.SCENE_VIDEO,
        )
