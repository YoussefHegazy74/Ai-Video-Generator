"""Optional real AI adapter: Gemini Omni Flash video generation.

Official capability notes (verified 2026-10-24 from
https://ai.google.dev/gemini-api/docs/omni):
- ``gemini-omni-1.1-flash`` generates video with audio.
- It supports 16:9/9:16 and 360p/720p/1080p/4K.
- It exposes no documented duration control. This adapter therefore never claims
  that the requested duration was honored; the measured duration is authoritative.
- The Interactions API is marked experimental by the installed SDK.

The adapter is contract-tested with a fake client. A live call requires a
GEMINI_API_KEY and is not part of ordinary automated tests.
"""

from __future__ import annotations

import base64
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from ai_video_generator.core.constants import TextRenderMode
from ai_video_generator.core.exceptions import (
    ProviderCapabilityError,
    ProviderError,
    ProviderOutputError,
    ProviderTimeoutError,
)
from ai_video_generator.integrations.audiovisual_generation_provider import (
    DurationSelection,
    GeneratedClip,
    GenerationRequest,
    ProviderCapabilities,
)

_PROMPT_VERSION = "gemini-omni-whiteboard-1"
_MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


class GeminiOmniProvider:
    capabilities = ProviderCapabilities(
        provider_id="gemini_omni",
        display_name="Gemini Omni Flash (real AI, optional)",
        generates_synchronized_audio=True,
        supports_duration_control=False,
        supported_durations_seconds=None,
        min_duration_seconds=None,
        max_duration_seconds=None,
        supports_separate_audio=False,
        supports_idempotency_key=False,
        supports_cancellation=False,
        output_container="mp4",
        text_render_mode=TextRenderMode.GENERATIVE,
        requires_credentials=True,
        is_local=False,
        notes=[
            "Generates native audio/video, but exposes no documented duration control.",
            "Technical text/code/equation fidelity is not guaranteed.",
            "No provider-side idempotency key; local duplicate suppression only.",
        ],
    )

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str = "gemini-omni-1.1-flash",
        aspect_ratio: str = "16:9",
        resolution: str = "720p",
        client: Any | None = None,
        client_factory: Callable[[str, float], Any] | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.aspect_ratio = aspect_ratio
        self.resolution = resolution
        self._client = client
        self._client_factory = client_factory

    def check_ready(self) -> None:
        if self._client is None:
            if not self.api_key:
                raise ProviderCapabilityError(
                    "GEMINI_API_KEY is not configured. Set it in the environment to "
                    "use the gemini_omni provider, or use the local "
                    "deterministic_whiteboard provider.",
                    provider=self.capabilities.provider_id,
                )
            try:
                import google.genai  # noqa: F401
            except ImportError as exc:
                raise ProviderCapabilityError(
                    "google-genai is not installed. Install the optional 'google' "
                    "extra to use the gemini_omni provider.",
                    provider=self.capabilities.provider_id,
                    cause=exc,
                ) from exc

    def resolve_duration(self, requested_seconds: float) -> DurationSelection:
        if requested_seconds <= 0:
            raise ProviderCapabilityError(
                "Requested duration must be positive",
                provider=self.capabilities.provider_id,
            )
        return DurationSelection(
            requested_seconds=requested_seconds,
            selected_seconds=requested_seconds,
            duration_control=False,
            note=(
                "Gemini Omni Flash exposes no documented duration control. The "
                "requested value is sent only as a prompt hint; actual duration is "
                "measured from the returned media."
            ),
        )

    def generate(self, request: GenerationRequest) -> GeneratedClip:
        self.check_ready()
        client = self._client or self._create_client(request.timeout_seconds)
        prompt = self._build_prompt(request)
        started = time.monotonic()
        try:
            interaction = client.interactions.create(
                model=self.model,
                input=prompt,
                response_format={
                    "type": "video",
                    "aspect_ratio": self.aspect_ratio,
                    "resolution": self.resolution,
                },
            )
        except Exception as exc:  # SDK boundary: preserve class/status details.
            elapsed = time.monotonic() - started
            status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
            class_name = type(exc).__name__
            timeout_like = "timeout" in class_name.lower() or status in {408, 504}
            retryable = timeout_like or status in {408, 429} or (
                isinstance(status, int) and 500 <= status <= 599
            )
            details = {
                "error_class": class_name,
                "status_code": status,
                "elapsed_seconds": elapsed,
            }
            if timeout_like:
                raise ProviderTimeoutError(
                    "Gemini Omni request timed out",
                    provider=self.capabilities.provider_id,
                    details=details,
                    cause=exc,
                ) from exc
            raise ProviderError(
                f"Gemini Omni request failed ({class_name})",
                provider=self.capabilities.provider_id,
                retryable=retryable,
                status_code=status if isinstance(status, int) else None,
                details=details,
                cause=exc,
            ) from exc

        elapsed = time.monotonic() - started
        status = _field(interaction, "status")
        status_value = getattr(status, "value", status)
        interaction_id = _field(interaction, "id")
        interaction_id = str(interaction_id) if interaction_id else None
        if status_value is not None and status_value not in {
            "completed",
            "succeeded",
            "success",
        }:
            raise ProviderOutputError(
                f"Gemini Omni interaction did not complete successfully: "
                f"{status_value}",
                provider=self.capabilities.provider_id,
                retryable=False,
                request_id=interaction_id,
                details={"interaction_status": str(status_value)},
            )

        output_video = _field(interaction, "output_video")
        if output_video is None:
            raise ProviderOutputError(
                "Gemini Omni response contained no output video",
                provider=self.capabilities.provider_id,
                retryable=True,
                request_id=interaction_id,
            )

        output_dir = Path(request.output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{request.scene.scene_id}.mp4"
        data = _field(output_video, "data")
        if data:
            try:
                if isinstance(data, str):
                    video_bytes = base64.b64decode(data, validate=True)
                elif isinstance(data, (bytes, bytearray)):
                    video_bytes = bytes(data)
                else:
                    raise TypeError(f"Unsupported video data type: {type(data).__name__}")
            except (ValueError, TypeError) as exc:
                raise ProviderOutputError(
                    "Gemini Omni returned invalid base64 video data",
                    provider=self.capabilities.provider_id,
                    retryable=True,
                    cause=exc,
                ) from exc
            output_path.write_bytes(video_bytes)
        else:
            uri = _field(output_video, "uri")
            if not uri:
                raise ProviderOutputError(
                    "Gemini Omni output contained neither video data nor a URI",
                    provider=self.capabilities.provider_id,
                    retryable=True,
                )
            self._download_uri(str(uri), output_path, request.timeout_seconds)

        if not output_path.is_file() or output_path.stat().st_size <= 0:
            raise ProviderOutputError(
                "Gemini Omni did not produce a non-empty video file",
                provider=self.capabilities.provider_id,
                retryable=True,
            )

        interaction_id = interaction_id or f"gemini-{uuid.uuid4().hex}"
        return GeneratedClip(
            video_path=output_path,
            provider=self.capabilities.provider_id,
            model=self.model,
            request_id=interaction_id,
            text_render_mode=TextRenderMode.GENERATIVE,
            renderer="gemini_omni",
            narration_sha256=None,
            provider_metadata={
                "interaction_id": interaction_id,
                "interaction_status": status_value,
                "model": self.model,
                "aspect_ratio": self.aspect_ratio,
                "resolution": self.resolution,
                "elapsed_seconds": elapsed,
                "prompt_version": _PROMPT_VERSION,
                "duration_control": False,
            },
            warnings=[
                "Gemini Omni exposes no documented duration control; actual duration "
                "was measured, not requested.",
                "Generative video text/code/equation accuracy is not verified.",
            ],
        )

    def _create_client(self, timeout_seconds: float) -> Any:
        if self._client_factory is not None:
            return self._client_factory(self.api_key or "", timeout_seconds)
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise ProviderCapabilityError(
                "google-genai is not installed",
                provider=self.capabilities.provider_id,
                cause=exc,
            ) from exc
        http_options_kwargs: dict[str, Any] = {
            "timeout": int(timeout_seconds * 1000),
        }
        retry_options = getattr(types, "HttpRetryOptions", None)
        if retry_options is not None:
            http_options_kwargs["retry_options"] = retry_options(attempts=1)
        http_options = types.HttpOptions(**http_options_kwargs)
        return genai.Client(api_key=self.api_key, http_options=http_options)

    def _build_prompt(self, request: GenerationRequest) -> str:
        scene = request.scene
        visual_lines = "\n".join(f"- {item}" for item in scene.visual.instructions)
        text_lines = (
            "\n".join(f"- {text}" for text in scene.expected_visible_texts)
            or "- (no authoritative on-screen text supplied)"
        )
        return (
            "Create one short educational whiteboard scene for a lesson video.\n"
            "Style requirements:\n"
            "- Clean digital whiteboard or writing surface, no visible teacher, "
            "presenter, avatar, face, or hands.\n"
            "- Progressive handwriting/drawing, highlights, arrows, and diagrams.\n"
            "- Consistent colors and layout suitable for an educational video.\n"
            "- Clear, calm narration that explains the scene's single learning "
            "objective.\n"
            "- Do not add titles, authors, page numbers, or claims not supplied below.\n\n"
            f"SCENE TITLE:\n{scene.title}\n\n"
            f"LEARNING OBJECTIVE:\n{scene.learning_objective}\n\n"
            f"NARRATION TO SPEAK:\n{scene.narration}\n\n"
            f"VISUAL INSTRUCTIONS:\n{visual_lines}\n\n"
            f"AUTHORITATIVE VISIBLE TEXT (reproduce as accurately as possible; "
            f"generative models may still misspell technical text):\n{text_lines}\n\n"
            f"TARGET DURATION HINT: approximately {request.requested_duration_seconds:.1f} "
            "seconds. No duration control is guaranteed by the API."
        )

    @staticmethod
    def _download_uri(uri: str, output_path: Path, timeout_seconds: float) -> None:
        import urllib.error
        import urllib.request

        parsed = urlparse(uri)
        if parsed.scheme != "https":
            raise ProviderOutputError(
                "Refusing to download Gemini output over a non-HTTPS URI",
                provider="gemini_omni",
                retryable=False,
            )
        host = (parsed.hostname or "").lower()
        allowed = (
            host.endswith(".googleapis.com")
            or host.endswith(".googleusercontent.com")
            or host == "googleapis.com"
            or host == "googleusercontent.com"
        )
        if not allowed:
            raise ProviderOutputError(
                "Gemini output URI host is not an approved Google media host",
                provider="gemini_omni",
                retryable=False,
                details={"host": host},
            )
        try:
            with urllib.request.urlopen(uri, timeout=timeout_seconds) as response:
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > _MAX_DOWNLOAD_BYTES:
                    raise ProviderOutputError(
                        "Gemini output video exceeds the local download size limit",
                        provider="gemini_omni",
                        retryable=False,
                        details={"content_length": content_length},
                    )
                chunks: list[bytes] = []
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > _MAX_DOWNLOAD_BYTES:
                        raise ProviderOutputError(
                            "Gemini output video exceeded the local download size limit",
                            provider="gemini_omni",
                            retryable=False,
                        )
                    chunks.append(chunk)
            output_path.write_bytes(b"".join(chunks))
        except ProviderOutputError:
            raise
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ProviderOutputError(
                "Failed to download Gemini output video",
                provider="gemini_omni",
                retryable=True,
                cause=exc,
            ) from exc
