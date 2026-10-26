from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_video_generator.services.storage_service import ArtifactStorageService  # noqa: E402
from ai_video_generator.storage.artifact_registry import ArtifactRegistry  # noqa: E402
from ai_video_generator.storage.file_storage import LocalFileStorage  # noqa: E402
from ai_video_generator.video.audio_video_validation import ValidationPolicy  # noqa: E402
from ai_video_generator.video.media_probe import FFmpegTools  # noqa: E402

from tests.helpers import require_ffmpeg  # noqa: E402


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return ROOT


@pytest.fixture()
def media_tools() -> FFmpegTools:
    tools = FFmpegTools(timeout_seconds=30)
    require_ffmpeg(tools)
    return tools


@pytest.fixture()
def file_storage(tmp_path: Path) -> LocalFileStorage:
    return LocalFileStorage(tmp_path / "artifacts", temp_root=tmp_path / "work")


@pytest.fixture()
def artifact_storage(file_storage: LocalFileStorage, tmp_path: Path) -> ArtifactStorageService:
    registry = ArtifactRegistry(tmp_path / "artifacts" / "artifact_registry.json")
    return ArtifactStorageService(file_storage, registry)


@pytest.fixture()
def validation_policy() -> ValidationPolicy:
    return ValidationPolicy(
        require_audio=True,
        duration_warning_tolerance_seconds=0.5,
        duration_failure_tolerance_seconds=1.0,
        min_scene_duration_seconds=0.5,
        max_scene_duration_seconds=30.0,
        sync_tolerance_seconds=0.35,
        sync_failure_tolerance_seconds=0.9,
        min_width=160,
        min_height=90,
        min_fps=5.0,
        min_media_bytes=1024,
        blank_frame_stddev_threshold=2.0,
    )
