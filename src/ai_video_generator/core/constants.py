"""Shared enums, state transitions, and stable constants.

This module contains no environment-specific values. It is the single source of
truth for scene/job states used by models, services, agents, and persistence.
"""

from __future__ import annotations

from enum import Enum
from typing import Final

SCENE_SCHEMA_VERSION: Final[str] = "1.0"
ASSEMBLY_SCHEMA_VERSION: Final[str] = "1.0"
VALIDATION_POLICY_VERSION: Final[str] = "1.0"


class SceneStatus(str, Enum):
    """Lifecycle of one scene.

    Legal transitions are defined in ``LEGAL_SCENE_TRANSITIONS``. A scene is
    never assembled unless it is ``VALIDATED``.
    """

    PENDING = "pending"
    GENERATING = "generating"
    GENERATED = "generated"
    VALIDATING = "validating"
    VALIDATED = "validated"
    REJECTED = "rejected"
    FAILED = "failed"


LEGAL_SCENE_TRANSITIONS: Final[dict[SceneStatus, frozenset[SceneStatus]]] = {
    SceneStatus.PENDING: frozenset({SceneStatus.GENERATING, SceneStatus.FAILED}),
    SceneStatus.GENERATING: frozenset({SceneStatus.GENERATED, SceneStatus.FAILED}),
    SceneStatus.GENERATED: frozenset(
        {SceneStatus.VALIDATING, SceneStatus.FAILED}
    ),
    SceneStatus.VALIDATING: frozenset(
        {SceneStatus.VALIDATED, SceneStatus.REJECTED, SceneStatus.FAILED}
    ),
    SceneStatus.VALIDATED: frozenset(
        {SceneStatus.VALIDATING, SceneStatus.REJECTED, SceneStatus.FAILED}
    ),
    SceneStatus.REJECTED: frozenset(
        {SceneStatus.GENERATING, SceneStatus.FAILED}
    ),
    SceneStatus.FAILED: frozenset({SceneStatus.GENERATING}),
}


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStage(str, Enum):
    PENDING = "pending"
    PREPARING = "preparing"
    GENERATING = "generating"
    VALIDATING = "validating"
    ASSEMBLING = "assembling"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CheckStatus(str, Enum):
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"
    UNAVAILABLE = "unavailable"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class MediaKind(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    MANIFEST = "manifest"
    IMAGE = "image"
    OTHER = "other"


class ArtifactKind(str, Enum):
    UPLOAD = "upload"
    EXTRACTED = "extracted"
    SCENE_VIDEO = "scene_video"
    SCENE_AUDIO = "scene_audio"
    SCENE_MANIFEST = "scene_manifest"
    SCENE_RECORD = "scene_record"
    VALIDATION_REPORT = "validation_report"
    FINAL_VIDEO = "final_video"
    ASSEMBLY_MANIFEST = "assembly_manifest"
    JOB_MANIFEST = "job_manifest"
    LOG = "log"


class TransitionStyle(str, Enum):
    """Assembly transitions that are actually implemented.

    ``FADE_BLACK`` is non-overlapping: each clip is faded to/from black at its
    own edges and then concatenated. Narration is therefore not overlapped or
    trimmed. Crossfades are intentionally unsupported.
    """

    CUT = "cut"
    FADE_BLACK = "fade_black"


class TextRenderMode(str, Enum):
    """How visible text was produced, used by text-accuracy validation."""

    DETERMINISTIC = "deterministic"
    GENERATIVE = "generative"
    UNKNOWN = "unknown"


BLOCKING_SEVERITIES: Final[frozenset[Severity]] = frozenset(
    {Severity.ERROR, Severity.CRITICAL}
)
