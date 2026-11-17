from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_video_generator.core.constants import TextRenderMode
from ai_video_generator.core.exceptions import (
    ProviderCapabilityError,
    ProviderError,
    ProviderOutputError,
    ProviderTimeoutError,
)
from ai_video_generator.integrations.audiovisual_generation_provider import (
    GenerationRequest,
)
from ai_video_generator.integrations.gemini_omni_provider import GeminiOmniProvider

from tests.helpers import make_scene


class _Interactions:
    def __init__(self, response=None, error: BaseException | None = None) -> None:
        self.response = response
        self.error = error
        self.kwargs: dict[str, object] | None = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return self.response


class _Client:
    def __init__(self, interactions: _Interactions) -> None:
        self.interactions = interactions


def _request(tmp_path: Path) -> GenerationRequest:
    scene = make_scene("gemini-scene", target_duration=5.0)
    return GenerationRequest(
        job_id="job_gemini",
        scene=scene,
        requested_duration_seconds=5.0,
        selected_duration_seconds=5.0,
        scene_count=1,
        output_dir=tmp_path,
        timeout_seconds=10.0,
        idempotency_key="a" * 64,
    )


def _response(*, data: bytes | None = b"fake-video-bytes", uri: str | None = None):
    output_video = SimpleNamespace(
        data=base64.b64encode(data).decode("ascii") if data is not None else None,
        uri=uri,
    )
    return SimpleNamespace(
        id="interaction-1",
        status="completed",
        output_video=output_video,
    )


def test_gemini_requires_credentials_without_a_client() -> None:
    provider = GeminiOmniProvider(api_key=None)
    with pytest.raises(ProviderCapabilityError):
        provider.check_ready()


def test_gemini_reports_no_duration_control() -> None:
    provider = GeminiOmniProvider(api_key="unused", client=_Client(_Interactions()))
    selection = provider.resolve_duration(12.5)
    assert selection.selected_seconds == 12.5
    assert selection.duration_control is False
    assert "no documented duration control" in (selection.note or "").lower()


def test_gemini_generate_writes_returned_data_and_builds_prompt(
    tmp_path: Path,
) -> None:
    interactions = _Interactions(_response(data=b"fake-video-bytes"))
    provider = GeminiOmniProvider(api_key=None, client=_Client(interactions))
    request = _request(tmp_path)

    clip = provider.generate(request)

    assert clip.video_path.is_file()
    assert clip.video_path.read_bytes() == b"fake-video-bytes"
    assert clip.provider == "gemini_omni"
    assert clip.text_render_mode == TextRenderMode.GENERATIVE
    assert clip.narration_sha256 is None
    assert clip.provider_metadata["duration_control"] is False
    assert clip.request_id == "interaction-1"

    assert interactions.kwargs is not None
    assert interactions.kwargs["model"] == "gemini-omni-1.1-flash"
    prompt = interactions.kwargs["input"]
    assert request.scene.narration in prompt
    assert request.scene.title in prompt
    assert "TARGET DURATION HINT" in prompt
    assert interactions.kwargs["response_format"] == {
        "type": "video",
        "aspect_ratio": "16:9",
        "resolution": "720p",
    }


def test_gemini_maps_rate_limit_errors_to_retryable_provider_errors(
    tmp_path: Path,
) -> None:
    class RateLimitedError(Exception):
        status_code = 429

    provider = GeminiOmniProvider(
        api_key=None,
        client=_Client(_Interactions(error=RateLimitedError("rate limited"))),
    )
    with pytest.raises(ProviderError) as error:
        provider.generate(_request(tmp_path))
    assert error.value.retryable is True
    assert error.value.status_code == 429
    assert error.value.details["error_class"] == "RateLimitedError"


def test_gemini_maps_timeouts_to_provider_timeout_errors(tmp_path: Path) -> None:
    class ClientTimeoutError(Exception):
        pass

    provider = GeminiOmniProvider(
        api_key=None,
        client=_Client(_Interactions(error=ClientTimeoutError("timed out"))),
    )
    with pytest.raises(ProviderTimeoutError) as error:
        provider.generate(_request(tmp_path))
    assert error.value.retryable is True


def test_gemini_refuses_non_https_output_uri(tmp_path: Path) -> None:
    provider = GeminiOmniProvider(
        api_key=None,
        client=_Client(
            _Interactions(_response(data=None, uri="http://example.com/video.mp4"))
        ),
    )
    with pytest.raises(ProviderOutputError) as error:
        provider.generate(_request(tmp_path))
    assert "non-HTTPS" in error.value.message


def test_gemini_rejects_response_without_video(tmp_path: Path) -> None:
    response = SimpleNamespace(id="interaction-2", status="completed", output_video=None)
    provider = GeminiOmniProvider(
        api_key=None,
        client=_Client(_Interactions(response)),
    )
    with pytest.raises(ProviderOutputError):
        provider.generate(_request(tmp_path))
