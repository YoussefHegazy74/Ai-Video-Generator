"""Backward-compatible domain import location for the canonical scene contract.

The actual definitions live in :mod:`ai_video_generator.video.scene_contract`.
This module intentionally re-exports them so existing code can import
``ai_video_generator.models.scene`` without creating a second schema.
"""

from ai_video_generator.video.scene_contract import (
    FailureDetail,
    GenerationInfo,
    MediaAsset,
    RetryInfo,
    SceneMedia,
    SceneSet,
    SceneSpec,
    SourceReference,
    SyncMetadata,
    TextElement,
    ValidationCheck,
    ValidationReport,
    VisualSpec,
)

__all__ = [
    "FailureDetail",
    "GenerationInfo",
    "MediaAsset",
    "RetryInfo",
    "SceneMedia",
    "SceneSet",
    "SceneSpec",
    "SourceReference",
    "SyncMetadata",
    "TextElement",
    "ValidationCheck",
    "ValidationReport",
    "VisualSpec",
]
