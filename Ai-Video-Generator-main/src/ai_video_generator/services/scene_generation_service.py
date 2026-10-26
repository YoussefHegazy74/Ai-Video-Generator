"""Scene generation service.

Responsibilities:
- enforce provider capabilities (synchronized audio, readiness);
- suppress duplicate local generation with a lock;
- retry only retryable provider failures, with bounded exponential backoff and
  jitter;
- store provider outputs through the storage abstraction and record measured
  FFprobe metadata;
- update the scene contract and persist a scene record.

Requested duration is never copied into measured metadata. Provider output is
always probed after generation.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from pathlib import Path
from typing import Callable

from ai_video_generator.core.constants import ArtifactKind, MediaKind, SceneStatus
from ai_video_generator.core.exceptions import (
    AppError,
    DuplicateGenerationError,
    PipelineCancelledError,
    ProviderCapabilityError,
    ProviderError,
    ProviderOutputError,
    SceneContractError,
    SceneGenerationError,
    SceneStateError,
)
from ai_video_generator.core.logging import get_logger
from ai_video_generator.integrations.audiovisual_generation_provider import (
    AudiovisualGenerationProvider,
    DurationSelection,
    GeneratedClip,
    GenerationRequest,
)
from ai_video_generator.services.storage_service import ArtifactStorageService
from ai_video_generator.utils.identifiers import safe_component
from ai_video_generator.video.media_probe import FFmpegTools, MediaInfo
from ai_video_generator.video.scene_contract import (
    FailureDetail,
    GenerationInfo,
    MediaAsset,
    RetryInfo,
    SceneMedia,
    SceneSpec,
    SyncMetadata,
)

logger = get_logger(__name__)

CancellationCheck = Callable[[], bool]


class SceneGenerationService:
    def __init__(
        self,
        provider: AudiovisualGenerationProvider,
        storage: ArtifactStorageService,
        media_tools: FFmpegTools,
        *,
        max_attempts: int = 3,
        timeout_seconds: float = 600.0,
        backoff_base_seconds: float = 1.0,
        backoff_max_seconds: float = 8.0,
        backoff_jitter_seconds: float = 0.25,
        sync_tolerance_seconds: float = 0.35,
        sleep: Callable[[float], None] = time.sleep,
        random_source: random.Random | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if backoff_base_seconds < 0 or backoff_max_seconds < 0:
            raise ValueError("Backoff values must be non-negative")
        if backoff_jitter_seconds < 0:
            raise ValueError("backoff_jitter_seconds must be non-negative")
        if sync_tolerance_seconds < 0:
            raise ValueError("sync_tolerance_seconds must be non-negative")
        self.provider = provider
        self.storage = storage
        self.media_tools = media_tools
        self.max_attempts = max_attempts
        self.timeout_seconds = timeout_seconds
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_max_seconds = backoff_max_seconds
        self.backoff_jitter_seconds = backoff_jitter_seconds
        self.sync_tolerance_seconds = sync_tolerance_seconds
        self._sleep = sleep
        self._random = random_source or random.Random()

    # ----------------------------------------------------------------- public API

    def generate_scene(
        self,
        scene: SceneSpec,
        *,
        job_id: str,
        scene_count: int,
        cancellation_check: CancellationCheck | None = None,
    ) -> SceneSpec:
        if scene_count < 1:
            raise SceneContractError("scene_count must be at least 1")
        if scene.scene_index >= scene_count:
            raise SceneContractError(
                f"Scene index {scene.scene_index} is outside the job's "
                f"{scene_count} scenes",
                details={"scene_id": scene.scene_id, "scene_count": scene_count},
            )

        reusable = self._reusable_scene(scene)
        if reusable is not None:
            logger.info(
                "Scene generation is already complete; returning stored result",
                extra={
                    "job_id": job_id,
                    "scene_id": scene.scene_id,
                    "provider": self.provider.capabilities.provider_id,
                },
            )
            return reusable

        if scene.status == SceneStatus.GENERATING:
            raise DuplicateGenerationError(
                f"Scene {scene.scene_id!r} is already marked as generating",
                details={"scene_id": scene.scene_id},
            )
        if scene.status not in {
            SceneStatus.PENDING,
            SceneStatus.FAILED,
            SceneStatus.REJECTED,
        }:
            raise SceneStateError(
                f"Scene {scene.scene_id!r} cannot be generated from status "
                f"{scene.status.value!r}",
                details={"scene_id": scene.scene_id, "status": scene.status.value},
            )

        if cancellation_check is not None and cancellation_check():
            raise PipelineCancelledError(
                f"Generation cancelled before scene {scene.scene_id!r} started",
                details={"scene_id": scene.scene_id},
            )

        capabilities = self.provider.capabilities
        if not capabilities.generates_synchronized_audio:
            raise ProviderCapabilityError(
                f"Provider {capabilities.provider_id!r} does not generate "
                "synchronized audio; silent scenes are rejected.",
                provider=capabilities.provider_id,
            )
        self.provider.check_ready()

        selection = self.provider.resolve_duration(scene.target_duration_seconds)
        if selection.selected_seconds <= 0:
            raise ProviderCapabilityError(
                "Provider selected a non-positive duration",
                provider=capabilities.provider_id,
            )

        idempotency_key = self._idempotency_key(scene, job_id, selection.selected_seconds)
        lock_key = (
            f"{self.storage.job_prefix(job_id)}/locks/"
            f"{scene.scene_index:03d}-{safe_component(scene.scene_id, field='scene_id')}.lock"
        )

        with self.storage.acquire_lock(lock_key):
            # Clear stale artifacts before a retry/re-generation so a failed
            # attempt cannot leave a previous validation report attached to a new
            # clip. This happens under the lock so a duplicate request does not
            # mutate the caller's scene object.
            if scene.status in {SceneStatus.FAILED, SceneStatus.REJECTED}:
                scene.validation = None
                scene.failure = None
                scene.media = SceneMedia()
                scene.generation = None
                scene.sync = None
            scene.transition_to(SceneStatus.GENERATING)

            with self.storage.temporary_directory(f"gen-{scene.scene_index:03d}") as work_dir:
                request = GenerationRequest(
                    job_id=job_id,
                    scene=scene,
                    requested_duration_seconds=scene.target_duration_seconds,
                    selected_duration_seconds=selection.selected_seconds,
                    scene_count=scene_count,
                    output_dir=work_dir,
                    timeout_seconds=self.timeout_seconds,
                    idempotency_key=idempotency_key,
                )
                try:
                    clip, video_info, audio_info, attempts = (
                        self._generate_with_retries(
                            scene,
                            request,
                            selection_note=selection.note,
                            cancellation_check=cancellation_check,
                        )
                    )
                except PipelineCancelledError as exc:
                    self._finalize_failure(
                        scene,
                        exc,
                        attempts=scene.retry.attempts,
                        job_id=job_id,
                    )
                    raise
                self._finalize_success(
                    scene,
                    clip=clip,
                    video_info=video_info,
                    audio_info=audio_info,
                    attempts=attempts,
                    selection=selection,
                    idempotency_key=idempotency_key,
                    job_id=job_id,
                )
        return scene

    # ------------------------------------------------------------------ internals

    def _reusable_scene(self, scene: SceneSpec) -> SceneSpec | None:
        if scene.status not in {SceneStatus.GENERATED, SceneStatus.VALIDATED}:
            return None
        if scene.media.video is None or scene.generation is None:
            return None
        if not self.storage.exists(scene.media.video.artifact_key):
            raise SceneGenerationError(
                "Scene is marked generated but its stored video is missing",
                scene_id=scene.scene_id,
                attempts=0,
                retryable=True,
                failure={"code": "generated_artifact_missing"},
            )
        return scene

    def _idempotency_key(
        self, scene: SceneSpec, job_id: str, selected_seconds: float
    ) -> str:
        payload = {
            "job_id": job_id,
            "scene_id": scene.scene_id,
            "scene_index": scene.scene_index,
            "narration_sha256": scene.narration_sha256,
            "target_duration_seconds": scene.target_duration_seconds,
            "selected_duration_seconds": selected_seconds,
            "provider": self.provider.capabilities.provider_id,
            "schema_version": scene.schema_version,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return hashlib.sha256(encoded).hexdigest()

    def _generate_with_retries(
        self,
        scene: SceneSpec,
        request: GenerationRequest,
        *,
        selection_note: str | None,
        cancellation_check: CancellationCheck | None,
    ) -> tuple[GeneratedClip, MediaInfo, MediaInfo | None, int]:
        attempts = 0
        last_code: str | None = None
        last_message: str | None = None
        while attempts < self.max_attempts:
            if cancellation_check is not None and cancellation_check():
                raise PipelineCancelledError(
                    f"Generation cancelled for scene {scene.scene_id!r}",
                    details={"scene_id": scene.scene_id},
                )
            attempts += 1
            try:
                clip = self.provider.generate(request)
                video_info, audio_info = self._inspect_clip(
                    clip, output_dir=Path(request.output_dir)
                )
                scene.retry = RetryInfo(
                    attempts=attempts,
                    max_attempts=self.max_attempts,
                    last_error_code=last_code,
                    last_error=last_message,
                    retryable=False,
                    next_retry_seconds=None,
                )
                logger.info(
                    "Scene generated",
                    extra={
                        "job_id": request.job_id,
                        "scene_id": scene.scene_id,
                        "provider": clip.provider,
                        "attempts": attempts,
                        "measured_duration_seconds": video_info.duration_seconds,
                    },
                )
                return clip, video_info, audio_info, attempts
            except ProviderError as exc:
                last_code = exc.code
                last_message = exc.message
                retryable = exc.retryable
                logger.warning(
                    "Provider generation attempt failed",
                    extra={
                        "job_id": request.job_id,
                        "scene_id": scene.scene_id,
                        "provider": self.provider.capabilities.provider_id,
                        "attempt": attempts,
                        "max_attempts": self.max_attempts,
                        "error_code": exc.code,
                        "retryable": retryable,
                    },
                )
                scene.retry = RetryInfo(
                    attempts=attempts,
                    max_attempts=self.max_attempts,
                    last_error_code=exc.code,
                    last_error=exc.message,
                    retryable=retryable,
                    next_retry_seconds=None,
                )
                if not retryable or attempts >= self.max_attempts:
                    self._finalize_failure(
                        scene,
                        exc,
                        attempts=attempts,
                        job_id=request.job_id,
                    )
                    raise SceneGenerationError(
                        f"Scene {scene.scene_id!r} generation failed after "
                        f"{attempts} attempt(s): {exc.message}",
                        scene_id=scene.scene_id,
                        attempts=attempts,
                        retryable=retryable,
                        failure=exc.as_dict(),
                        cause=exc,
                    ) from exc
                delay = self._backoff_delay(attempts)
                scene.retry.next_retry_seconds = delay
                logger.info(
                    "Retrying scene generation after backoff",
                    extra={
                        "job_id": request.job_id,
                        "scene_id": scene.scene_id,
                        "attempt": attempts,
                        "delay_seconds": round(delay, 3),
                    },
                )
                self._sleep(delay)
            except PipelineCancelledError:
                raise
            except AppError as exc:
                # Unexpected application failure from a provider adapter. Mark the
                # scene failed, then surface a typed generation error.
                self._finalize_failure(
                    scene,
                    exc,
                    attempts=attempts,
                    job_id=request.job_id,
                )
                raise SceneGenerationError(
                    f"Scene {scene.scene_id!r} generation failed: {exc.message}",
                    scene_id=scene.scene_id,
                    attempts=attempts,
                    retryable=False,
                    failure=exc.as_dict(),
                    cause=exc,
                ) from exc
            except Exception as exc:  # pragma: no cover - defensive adapter boundary
                self._finalize_failure(
                    scene,
                    exc,
                    attempts=attempts,
                    job_id=request.job_id,
                )
                raise SceneGenerationError(
                    f"Scene {scene.scene_id!r} generation raised an unexpected "
                    f"{type(exc).__name__}",
                    scene_id=scene.scene_id,
                    attempts=attempts,
                    retryable=False,
                    failure={"code": "unexpected_provider_error"},
                    cause=exc,
                ) from exc

        raise SceneGenerationError(  # pragma: no cover - loop always returns/raises
            f"Scene {scene.scene_id!r} exhausted generation attempts",
            scene_id=scene.scene_id,
            attempts=attempts,
            retryable=True,
        )

    def _backoff_delay(self, attempt: int) -> float:
        exponential = self.backoff_base_seconds * (2 ** (attempt - 1))
        capped = min(exponential, self.backoff_max_seconds)
        jitter = (
            self._random.uniform(0, self.backoff_jitter_seconds)
            if self.backoff_jitter_seconds > 0
            else 0.0
        )
        return capped + jitter

    def _inspect_clip(
        self, clip: GeneratedClip, *, output_dir: Path
    ) -> tuple[MediaInfo, MediaInfo | None]:
        root = output_dir.resolve()
        for label, path in (
            ("video", clip.video_path),
            ("manifest", clip.manifest_path),
            ("audio", clip.separate_audio_path),
        ):
            if path is None:
                continue
            resolved = Path(path).resolve()
            if not resolved.is_relative_to(root):
                raise ProviderOutputError(
                    f"Provider returned a {label} path outside the generation "
                    "temporary directory",
                    provider=clip.provider,
                    retryable=False,
                )
            if not resolved.is_file() or resolved.stat().st_size <= 0:
                raise ProviderOutputError(
                    f"Provider returned a missing or empty {label} artifact",
                    provider=clip.provider,
                    retryable=True,
                    details={"path": str(resolved)},
                )
            if label == "video":
                clip.video_path = resolved
            elif label == "manifest":
                clip.manifest_path = resolved
            else:
                clip.separate_audio_path = resolved

        try:
            video_info = self.media_tools.probe(clip.video_path)
        except AppError as exc:
            raise ProviderOutputError(
                "Generated video could not be probed",
                provider=clip.provider,
                retryable=True,
                details=exc.details,
                cause=exc,
            ) from exc
        if video_info.video is None:
            raise ProviderOutputError(
                "Generated media contains no video stream",
                provider=clip.provider,
                retryable=True,
            )
        if video_info.audio is None:
            raise ProviderOutputError(
                "Generated media contains no narration audio; refusing silent output",
                provider=clip.provider,
                retryable=True,
            )

        audio_info: MediaInfo | None = None
        if clip.separate_audio_path is not None:
            try:
                audio_info = self.media_tools.probe(clip.separate_audio_path)
            except AppError as exc:
                raise ProviderOutputError(
                    "Generated separate audio could not be probed",
                    provider=clip.provider,
                    retryable=True,
                    details=exc.details,
                    cause=exc,
                ) from exc
            if audio_info.audio is None:
                raise ProviderOutputError(
                    "Generated separate audio contains no audio stream",
                    provider=clip.provider,
                    retryable=True,
                )
        return video_info, audio_info

    def _finalize_success(
        self,
        scene: SceneSpec,
        *,
        clip: GeneratedClip,
        video_info: MediaInfo,
        audio_info: MediaInfo | None,
        attempts: int,
        selection: DurationSelection,
        idempotency_key: str,
        job_id: str,
    ) -> None:
        prefix = self.storage.scene_prefix(job_id, scene.scene_index, scene.scene_id)
        video_suffix = clip.video_path.suffix.lower() or ".mp4"
        video_record = self.storage.store_file(
            clip.video_path,
            f"{prefix}/video{video_suffix}",
            ArtifactKind.SCENE_VIDEO,
            job_id=job_id,
            scene_id=scene.scene_id,
            metadata={"provider": clip.provider, "model": clip.model},
        )
        video_asset = self._video_asset(video_record.key, video_record.sha256, video_record.size_bytes, video_info)

        manifest_asset: MediaAsset | None = None
        if clip.manifest_path is not None:
            manifest_record = self.storage.store_file(
                clip.manifest_path,
                f"{prefix}/manifest.json",
                ArtifactKind.SCENE_MANIFEST,
                job_id=job_id,
                scene_id=scene.scene_id,
                metadata={"provider": clip.provider},
            )
            manifest_asset = MediaAsset(
                artifact_key=manifest_record.key,
                kind=MediaKind.MANIFEST,
                mime_type="application/json",
                size_bytes=manifest_record.size_bytes,
                sha256=manifest_record.sha256,
            )

        audio_asset: MediaAsset | None = None
        if clip.separate_audio_path is not None and audio_info is not None:
            audio_suffix = clip.separate_audio_path.suffix.lower() or ".wav"
            audio_record = self.storage.store_file(
                clip.separate_audio_path,
                f"{prefix}/narration{audio_suffix}",
                ArtifactKind.SCENE_AUDIO,
                job_id=job_id,
                scene_id=scene.scene_id,
                metadata={"provider": clip.provider},
            )
            audio_asset = self._audio_asset(
                audio_record.key,
                audio_record.sha256,
                audio_record.size_bytes,
                audio_info,
            )

        scene.media = SceneMedia(
            video=video_asset,
            audio=audio_asset,
            manifest=manifest_asset,
            audio_embedded=audio_asset is None,
        )
        scene.generation = GenerationInfo(
            provider=clip.provider,
            model=clip.model,
            request_id=clip.request_id,
            requested_duration_seconds=scene.target_duration_seconds,
            selected_duration_seconds=selection.selected_seconds,
            duration_control=selection.duration_control,
            duration_note=selection.note,
            text_render_mode=clip.text_render_mode,
            renderer=clip.renderer,
            narration_sha256=clip.narration_sha256,
            input_fingerprint=idempotency_key,
            provider_metadata=clip.provider_metadata,
        )
        scene.sync = self._sync_metadata(video_info, audio_info)
        scene.validation = None
        scene.failure = None
        scene.transition_to(SceneStatus.GENERATED)
        self._persist_scene_record(scene, job_id, attempts=attempts)
        logger.info(
            "Scene generation finalized",
            extra={
                "job_id": job_id,
                "scene_id": scene.scene_id,
                "attempts": attempts,
                "video_key": video_asset.artifact_key,
                "measured_duration_seconds": video_asset.duration_seconds,
            },
        )

    def _finalize_failure(
        self,
        scene: SceneSpec,
        error: BaseException,
        *,
        attempts: int,
        job_id: str,
    ) -> None:
        if isinstance(error, AppError):
            code = error.code
            message = error.message
            retryable = error.retryable
            details = error.details
        else:
            code = "unexpected_provider_error"
            message = f"Unexpected {type(error).__name__} during generation"
            retryable = False
            details = {}
        scene.failure = FailureDetail(
            code=code,
            message=message,
            stage="generation",
            retryable=retryable,
            details=details,
        )
        scene.retry = RetryInfo(
            attempts=attempts,
            max_attempts=self.max_attempts,
            last_error_code=code,
            last_error=message,
            retryable=retryable,
            next_retry_seconds=None,
        )
        if scene.status == SceneStatus.GENERATING:
            scene.transition_to(SceneStatus.FAILED)
        try:
            self._persist_scene_record(scene, job_id, attempts=attempts)
        except AppError:
            logger.exception(
                "Failed to persist failed scene record",
                extra={"job_id": job_id, "scene_id": scene.scene_id},
            )

    def _persist_scene_record(
        self, scene: SceneSpec, job_id: str, *, attempts: int
    ) -> None:
        prefix = self.storage.scene_prefix(job_id, scene.scene_index, scene.scene_id)
        self.storage.store_json(
            scene.model_dump(mode="json"),
            f"{prefix}/scene.json",
            ArtifactKind.SCENE_RECORD,
            job_id=job_id,
            scene_id=scene.scene_id,
            metadata={"status": scene.status.value, "attempts": attempts},
        )

    def _video_asset(
        self,
        key: str,
        sha256: str,
        size_bytes: int,
        info: MediaInfo,
    ) -> MediaAsset:
        return MediaAsset(
            artifact_key=key,
            kind=MediaKind.VIDEO,
            mime_type="video/mp4",
            size_bytes=size_bytes,
            sha256=sha256,
            duration_seconds=info.duration_seconds,
            width=info.video.width if info.video else None,
            height=info.video.height if info.video else None,
            fps=info.video.fps if info.video else None,
            video_codec=info.video.codec_name if info.video else None,
            audio_codec=info.audio.codec_name if info.audio else None,
            container=info.format_name,
            has_audio=info.audio is not None,
            sample_rate=info.audio.sample_rate if info.audio else None,
            channels=info.audio.channels if info.audio else None,
            video_streams=info.video_streams,
            audio_streams=info.audio_streams,
        )

    def _audio_asset(
        self,
        key: str,
        sha256: str,
        size_bytes: int,
        info: MediaInfo,
    ) -> MediaAsset:
        return MediaAsset(
            artifact_key=key,
            kind=MediaKind.AUDIO,
            mime_type="audio/wav",
            size_bytes=size_bytes,
            sha256=sha256,
            duration_seconds=info.duration_seconds,
            audio_codec=info.audio.codec_name if info.audio else None,
            container=info.format_name,
            has_audio=info.audio is not None,
            sample_rate=info.audio.sample_rate if info.audio else None,
            channels=info.audio.channels if info.audio else None,
            audio_streams=info.audio_streams,
        )

    def _sync_metadata(
        self, video_info: MediaInfo, audio_info: MediaInfo | None
    ) -> SyncMetadata:
        if audio_info is None:
            delta = video_info.audio_video_duration_delta_seconds
            start_delta = video_info.audio_video_start_delta_seconds
            notes = [
                "Audio is embedded in the generated video; stream timing was "
                "measured with FFprobe."
            ]
        else:
            video_duration = video_info.video_duration_seconds
            audio_duration = audio_info.audio_duration_seconds
            delta = (
                abs(video_duration - audio_duration)
                if video_duration is not None and audio_duration is not None
                else None
            )
            start_delta = None
            notes = [
                "Separate narration audio was generated; track timing was "
                "measured with FFprobe."
            ]
        return SyncMetadata(
            audio_video_duration_delta_seconds=delta,
            audio_start_offset_seconds=start_delta,
            tolerance_seconds=self.sync_tolerance_seconds,
            within_tolerance=(
                delta <= self.sync_tolerance_seconds if delta is not None else None
            ),
            semantic_alignment_checked=False,
            notes=notes
            + ["Semantic narration-to-visual alignment is not evaluated."],
        )


__all__ = ["CancellationCheck", "SceneGenerationService"]
