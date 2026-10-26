"""Canonical, provider-neutral scene contract.

This module is the single definition of a scene. It is used by generation,
validation, assembly, persistence, and (in future) the API layer. Other layers
must import these models rather than redefining scene fields.

Design rules:
- Requested durations and measured durations are separate fields.
- Artifact references are storage keys, never absolute paths.
- A scene cannot be validated or assembled without generated media.
- Validation failures are structured and machine-readable.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from ai_video_generator.core.constants import (
    BLOCKING_SEVERITIES,
    LEGAL_SCENE_TRANSITIONS,
    SCENE_SCHEMA_VERSION,
    VALIDATION_POLICY_VERSION,
    CheckStatus,
    MediaKind,
    SceneStatus,
    Severity,
    TextRenderMode,
)
from ai_video_generator.core.exceptions import SceneContractError, SceneStateError
from ai_video_generator.utils.paths import normalize_artifact_key

_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SECRET_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "credential",
    "password",
    "secret",
    "token",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sanitize_mapping(value: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, item in value.items():
        if any(marker in key.lower() for marker in _SECRET_MARKERS):
            sanitized[key] = "<redacted>"
        elif isinstance(item, dict):
            sanitized[key] = _sanitize_mapping(item)
        else:
            sanitized[key] = item
    return sanitized


class SourceReference(BaseModel):
    """Reference to authoritative source material for a scene."""

    model_config = ConfigDict(extra="forbid")

    document_id: str | None = None
    page_numbers: list[int] = Field(default_factory=list)
    reference: str | None = None
    excerpt: str | None = None

    @field_validator("page_numbers")
    @classmethod
    def _valid_pages(cls, value: list[int]) -> list[int]:
        if any(page < 1 for page in value):
            raise ValueError("page_numbers must contain positive page numbers")
        if len(set(value)) != len(value):
            raise ValueError("page_numbers must not contain duplicates")
        return value


class TextElement(BaseModel):
    """A piece of text intentionally shown on the whiteboard."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    kind: Literal["title", "body", "code", "equation", "label", "caption"] = "body"
    emphasis: bool = False

    @field_validator("text")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Text element must not be blank")
        return value


class VisualSpec(BaseModel):
    """Whiteboard style and deterministic visual instructions."""

    model_config = ConfigDict(extra="forbid")

    whiteboard_style: str = Field(default="clean_whiteboard", min_length=1)
    background_color: str = "#F8FAFC"
    accent_color: str = "#2563EB"
    instructions: list[str] = Field(min_length=1)
    text_elements: list[TextElement] = Field(default_factory=list)
    drawing_steps: list[str] = Field(default_factory=list)
    show_scene_title: bool = True
    allow_presenter: Literal[False] = False

    @field_validator("whiteboard_style", "instructions", "drawing_steps")
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("Visual fields must not be blank")
            return value
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("Visual instructions must not contain blank items")
        return cleaned

    @field_validator("background_color", "accent_color")
    @classmethod
    def _valid_color(cls, value: str) -> str:
        if not _HEX_COLOR_RE.fullmatch(value):
            raise ValueError("Colors must use #RRGGBB format")
        return value.upper()


class MediaAsset(BaseModel):
    """Measured metadata for one stored media artifact."""

    model_config = ConfigDict(extra="forbid")

    artifact_key: str
    kind: MediaKind
    mime_type: str = "application/octet-stream"
    size_bytes: int = Field(ge=0)
    sha256: str
    duration_seconds: float | None = Field(default=None, gt=0)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    fps: float | None = Field(default=None, gt=0)
    video_codec: str | None = None
    audio_codec: str | None = None
    container: str | None = None
    has_audio: bool | None = None
    sample_rate: int | None = Field(default=None, gt=0)
    channels: int | None = Field(default=None, gt=0)
    video_streams: int = Field(default=0, ge=0)
    audio_streams: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("artifact_key")
    @classmethod
    def _valid_key(cls, value: str) -> str:
        return normalize_artifact_key(value)

    @field_validator("sha256")
    @classmethod
    def _valid_sha(cls, value: str) -> str:
        value = value.lower()
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("sha256 must be a 64-character hexadecimal digest")
        return value

    @field_validator("kind")
    @classmethod
    def _kind_video_audio(cls, value: MediaKind) -> MediaKind:
        return value


class SceneMedia(BaseModel):
    """Artifact references for a generated scene."""

    model_config = ConfigDict(extra="forbid")

    video: MediaAsset | None = None
    audio: MediaAsset | None = None
    manifest: MediaAsset | None = None
    extras: dict[str, MediaAsset] = Field(default_factory=dict)
    audio_embedded: bool = False

    @model_validator(mode="after")
    def _embedded_audio_consistency(self) -> Self:
        if self.audio_embedded and self.video is not None:
            if self.video.has_audio is False:
                raise ValueError(
                    "audio_embedded is true but the video metadata reports no audio"
                )
        return self

    @property
    def has_audio(self) -> bool:
        return bool(self.audio is not None or (self.video and self.video.has_audio))


class GenerationInfo(BaseModel):
    """Provider provenance and requested/selected duration information."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    model: str | None = None
    request_id: str | None = None
    requested_duration_seconds: float = Field(gt=0)
    selected_duration_seconds: float | None = Field(default=None, gt=0)
    duration_control: bool = False
    duration_note: str | None = None
    text_render_mode: TextRenderMode = TextRenderMode.UNKNOWN
    renderer: str | None = None
    narration_sha256: str | None = None
    input_fingerprint: str | None = None
    generated_at: datetime = Field(default_factory=utc_now)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("narration_sha256")
    @classmethod
    def _valid_narration_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.lower()
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("narration_sha256 must be a hexadecimal SHA-256 digest")
        return value

    @field_validator("provider_metadata")
    @classmethod
    def _no_secrets(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _sanitize_mapping(value)


class SyncMetadata(BaseModel):
    """Measurable audio/video alignment information.

    This does not assert semantic synchronization between narration and visual
    events; that requires human or model review and is tracked separately.
    """

    model_config = ConfigDict(extra="forbid")

    audio_video_duration_delta_seconds: float | None = Field(default=None, ge=0)
    audio_start_offset_seconds: float | None = None
    tolerance_seconds: float = Field(default=0.35, ge=0)
    within_tolerance: bool | None = None
    semantic_alignment_checked: bool = False
    notes: list[str] = Field(default_factory=list)
    measured_at: datetime = Field(default_factory=utc_now)


class RetryInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=1, ge=1)
    last_error_code: str | None = None
    last_error: str | None = None
    retryable: bool = False
    next_retry_seconds: float | None = Field(default=None, ge=0)


class FailureDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    stage: str
    retryable: bool = False
    check_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=utc_now)

    @field_validator("details")
    @classmethod
    def _no_secrets(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _sanitize_mapping(value)


class ValidationCheck(BaseModel):
    """One measurable or explicitly unavailable validation check."""

    model_config = ConfigDict(extra="forbid")

    check_id: str
    category: str
    status: CheckStatus
    severity: Severity = Severity.ERROR
    message: str
    expected: Any | None = None
    observed: Any | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    blocking: bool = True
    retryable: bool = False
    duration_ms: float | None = Field(default=None, ge=0)

    @property
    def blocks_assembly(self) -> bool:
        return (
            self.status == CheckStatus.FAIL
            and self.blocking
            and self.severity in BLOCKING_SEVERITIES
        )


class ValidationReport(BaseModel):
    """Structured validation result for one scene or the scene set."""

    model_config = ConfigDict(extra="forbid")

    scene_id: str
    policy_version: str = VALIDATION_POLICY_VERSION
    checks: list[ValidationCheck] = Field(default_factory=list)
    block_on_warnings: bool = False
    checked_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _unique_checks(self) -> Self:
        ids = [check.check_id for check in self.checks]
        if len(ids) != len(set(ids)):
            raise ValueError("Validation check_id values must be unique")
        return self

    @property
    def failures(self) -> list[ValidationCheck]:
        return [c for c in self.checks if c.status == CheckStatus.FAIL]

    @property
    def warnings(self) -> list[ValidationCheck]:
        return [c for c in self.checks if c.status == CheckStatus.WARNING]

    @property
    def unavailable(self) -> list[ValidationCheck]:
        return [c for c in self.checks if c.status == CheckStatus.UNAVAILABLE]

    @property
    def blocking_failures(self) -> list[ValidationCheck]:
        blocking = [c for c in self.failures if c.blocks_assembly]
        if self.block_on_warnings:
            blocking.extend(c for c in self.warnings if c.blocking)
        return blocking

    @property
    def is_blocked(self) -> bool:
        return bool(self.blocking_failures)

    @property
    def passed(self) -> bool:
        return not self.is_blocked

    @property
    def retry_recommended(self) -> bool:
        return any(check.retryable for check in self.blocking_failures)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def blocking_failure_count(self) -> int:
        return len(self.blocking_failures)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unavailable_count(self) -> int:
        return len(self.unavailable)

    def human_summary(self) -> str:
        lines = [
            f"Scene {self.scene_id}: "
            f"{'BLOCKED' if self.is_blocked else 'PASSED'} "
            f"({self.blocking_failure_count} blocking, {self.warning_count} warnings, "
            f"{self.unavailable_count} unavailable)"
        ]
        for check in self.checks:
            if check.status in {CheckStatus.FAIL, CheckStatus.WARNING, CheckStatus.UNAVAILABLE}:
                lines.append(
                    f"- [{check.status.value}/{check.severity.value}] "
                    f"{check.check_id}: {check.message}"
                )
        return "\n".join(lines)


class SceneSpec(BaseModel):
    """One coherent educational scene moving through the pipeline."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = SCENE_SCHEMA_VERSION
    scene_id: str
    scene_index: int = Field(ge=0)
    title: str = Field(min_length=1)
    learning_objective: str = Field(min_length=1)
    section_title: str | None = None
    source_references: list[SourceReference] = Field(default_factory=list)
    narration: str = Field(min_length=1)
    narration_reference: str | None = None
    visual: VisualSpec
    target_duration_seconds: float = Field(gt=0, le=600)
    status: SceneStatus = SceneStatus.PENDING
    media: SceneMedia = Field(default_factory=SceneMedia)
    generation: GenerationInfo | None = None
    sync: SyncMetadata | None = None
    retry: RetryInfo = Field(default_factory=RetryInfo)
    validation: ValidationReport | None = None
    failure: FailureDetail | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("scene_id")
    @classmethod
    def _valid_scene_id(cls, value: str) -> str:
        if not re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", value):
            raise ValueError(
                "scene_id must be 1-64 characters using letters, digits, dot, "
                "underscore, or hyphen, starting with a letter or digit"
            )
        return value

    @field_validator("title", "learning_objective", "narration", "section_title")
    @classmethod
    def _strip_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Scene text fields must not be blank")
        return value

    @model_validator(mode="after")
    def _state_invariants(self) -> Self:
        media_statuses = {
            SceneStatus.GENERATED,
            SceneStatus.VALIDATING,
            SceneStatus.VALIDATED,
            SceneStatus.REJECTED,
        }
        if self.status in media_statuses and self.media.video is None:
            raise ValueError(f"A {self.status.value} scene must reference generated video")
        if self.status in media_statuses and self.generation is None:
            raise ValueError(f"A {self.status.value} scene must have generation metadata")
        if self.status == SceneStatus.VALIDATED:
            if self.validation is None:
                raise ValueError("A validated scene must include a validation report")
            if self.validation.is_blocked:
                raise ValueError("A validated scene cannot have blocking validation failures")
        if self.status == SceneStatus.REJECTED and self.validation is None:
            raise ValueError("A rejected scene must include a validation report")
        if self.status == SceneStatus.FAILED and self.failure is None:
            raise ValueError("A failed scene must include failure details")
        if self.status == SceneStatus.GENERATING and self.validation is not None:
            raise ValueError("A generating scene must not retain a prior validation report")
        if self.visual.allow_presenter:
            raise ValueError("Presenter video is not permitted by the product contract")
        return self

    @property
    def expected_visible_texts(self) -> list[str]:
        texts = [element.text for element in self.visual.text_elements]
        if self.visual.show_scene_title:
            texts.insert(0, self.title)
        return texts

    @property
    def narration_sha256(self) -> str:
        return hashlib.sha256(self.narration.encode("utf-8")).hexdigest()

    def transition_to(self, new_status: SceneStatus) -> None:
        if new_status == self.status:
            return
        allowed = LEGAL_SCENE_TRANSITIONS.get(self.status, frozenset())
        if new_status not in allowed:
            raise SceneStateError(
                f"Illegal scene transition {self.status.value!r} -> "
                f"{new_status.value!r} for {self.scene_id}",
                details={
                    "scene_id": self.scene_id,
                    "from": self.status.value,
                    "to": new_status.value,
                    "allowed": sorted(item.value for item in allowed),
                },
            )
        self.status = new_status
        self.updated_at = utc_now()

    def assembly_blockers(self) -> list[str]:
        blockers: list[str] = []
        if self.status != SceneStatus.VALIDATED:
            blockers.append(
                f"scene {self.scene_id!r} status is {self.status.value!r}, "
                "expected 'validated'"
            )
        if self.validation is None:
            blockers.append(f"scene {self.scene_id!r} has no validation report")
        elif self.validation.is_blocked:
            blockers.append(
                f"scene {self.scene_id!r} has "
                f"{self.validation.blocking_failure_count} blocking validation failure(s)"
            )
        if self.media.video is None:
            blockers.append(f"scene {self.scene_id!r} has no generated video")
        if self.generation is None:
            blockers.append(f"scene {self.scene_id!r} has no generation metadata")
        return blockers


class SceneSet(BaseModel):
    """Ordered collection of all scenes required for one video job."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = SCENE_SCHEMA_VERSION
    project_id: str | None = None
    course_title: str | None = None
    expected_style: str | None = None
    scenes: list[SceneSpec] = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("course_title", "expected_style")
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def _unique_and_ordered(self) -> Self:
        ids = [scene.scene_id for scene in self.scenes]
        if len(ids) != len(set(ids)):
            raise ValueError("Scene IDs must be unique within a scene set")
        indices = sorted(scene.scene_index for scene in self.scenes)
        if len(indices) != len(set(indices)):
            raise ValueError("Scene indices must be unique within a scene set")
        if indices != list(range(len(self.scenes))):
            raise ValueError(
                "Scene indices must be contiguous and start at 0; "
                f"received {indices}"
            )
        return self

    @property
    def ordered_scenes(self) -> list[SceneSpec]:
        return sorted(self.scenes, key=lambda scene: scene.scene_index)

    def require_assembly_ready(self) -> None:
        issues: list[str] = []
        for scene in self.ordered_scenes:
            issues.extend(scene.assembly_blockers())
        if issues:
            raise SceneContractError(
                "Scene set is not ready for assembly: " + "; ".join(issues),
                details={"issues": issues},
            )

    def replace_scene(self, updated: SceneSpec) -> None:
        for index, scene in enumerate(self.scenes):
            if scene.scene_id == updated.scene_id:
                self.scenes[index] = updated
                self.updated_at = utc_now()
                return
        raise SceneContractError(
            f"Scene {updated.scene_id!r} does not belong to this scene set",
            details={"scene_id": updated.scene_id},
        )
