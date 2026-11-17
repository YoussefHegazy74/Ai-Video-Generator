"""Local narration synthesis adapters (no network, no paid services).

Preferred adapter: Windows System.Speech via PowerShell, which produces a WAV
file with an installed system voice. Fallback: FFmpeg's optional ``flite`` source
filter when the FFmpeg build includes it. If neither is available, generation
fails clearly rather than assembling a video with silent narration.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Protocol, runtime_checkable

from ai_video_generator.core.exceptions import TtsUnavailableError

_SAPI_SCRIPT = r"""
param(
    [Parameter(Mandatory=$true)][string]$TextFile,
    [Parameter(Mandatory=$true)][string]$OutputFile,
    [int]$Rate = 0
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$text = [System.IO.File]::ReadAllText($TextFile, [System.Text.Encoding]::UTF8)
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $synth.Rate = $Rate
    $synth.Volume = 100
    $synth.SetOutputToWaveFile($OutputFile)
    $synth.Speak($text)
}
finally {
    $synth.Dispose()
}
""".strip()


@runtime_checkable
class NarrationSynthesizer(Protocol):
    name: str

    def available(self) -> bool: ...

    def synthesize(
        self, text: str, output_path: Path, *, timeout_seconds: float = 60.0
    ) -> None: ...


def _validate_wav(path: Path) -> None:
    if not path.is_file() or path.stat().st_size <= 44:
        raise TtsUnavailableError(
            "Narration synthesis did not produce a non-empty WAV file",
            details={"path": str(path)},
        )
    try:
        with wave.open(str(path), "rb") as handle:
            if handle.getnframes() <= 0 or handle.getframerate() <= 0:
                raise TtsUnavailableError(
                    "Narration WAV has no audio frames",
                    details={"path": str(path)},
                )
    except wave.Error as exc:
        raise TtsUnavailableError(
            "Narration synthesizer produced an invalid WAV file",
            details={"path": str(path)},
            cause=exc,
        ) from exc


class WindowsSapiNarrator:
    name = "windows_sapi"

    def __init__(self, *, rate: int = 0) -> None:
        self.rate = max(-10, min(10, int(rate)))
        self.powershell = shutil.which("powershell.exe") or shutil.which("powershell")

    def available(self) -> bool:
        return sys.platform == "win32" and self.powershell is not None

    def synthesize(
        self, text: str, output_path: Path, *, timeout_seconds: float = 60.0
    ) -> None:
        if not self.available():
            raise TtsUnavailableError(
                "Windows SAPI narration is unavailable on this host",
                details={"platform": sys.platform},
            )
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="aivg-tts-") as temp:
            temp_path = Path(temp)
            text_path = temp_path / "narration.txt"
            script_path = temp_path / "speak.ps1"
            text_path.write_text(text, encoding="utf-8")
            script_path.write_text(_SAPI_SCRIPT, encoding="utf-8")
            command = [
                str(self.powershell),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                "-TextFile",
                str(text_path),
                "-OutputFile",
                str(output_path),
                "-Rate",
                str(self.rate),
            ]
            try:
                result = subprocess.run(
                    command,
                    shell=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_seconds,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise TtsUnavailableError(
                    f"Windows SAPI narration timed out after {timeout_seconds:.1f}s",
                    details={"timeout_seconds": timeout_seconds},
                    cause=exc,
                ) from exc
            except FileNotFoundError as exc:
                raise TtsUnavailableError(
                    "PowerShell executable was not found",
                    cause=exc,
                ) from exc
            if result.returncode != 0:
                raise TtsUnavailableError(
                    "Windows SAPI narration failed",
                    details={
                        "exit_code": result.returncode,
                        "stderr_tail": (result.stderr or "").strip().splitlines()[-8:],
                    },
                )
        _validate_wav(output_path)


class FliteNarrator:
    name = "ffmpeg_flite"

    def __init__(self, ffmpeg_path: str = "ffmpeg") -> None:
        self.ffmpeg_path = ffmpeg_path

    def available(self) -> bool:
        return shutil.which(self.ffmpeg_path) is not None or Path(
            self.ffmpeg_path
        ).is_file()

    def synthesize(
        self, text: str, output_path: Path, *, timeout_seconds: float = 60.0
    ) -> None:
        if not self.available():
            raise TtsUnavailableError("FFmpeg flite narration is unavailable")
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="aivg-tts-") as temp:
            temp_path = Path(temp)
            text_path = temp_path / "narration.txt"
            text_path.write_text(text, encoding="utf-8")
            command = [
                self.ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-f",
                "lavfi",
                "-i",
                "flite=textfile=narration.txt",
                "-ac",
                "1",
                "-ar",
                "22050",
                "-c:a",
                "pcm_s16le",
                "-y",
                str(output_path),
            ]
            try:
                result = subprocess.run(
                    command,
                    cwd=str(temp_path),
                    shell=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_seconds,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise TtsUnavailableError(
                    f"FFmpeg flite narration timed out after {timeout_seconds:.1f}s",
                    details={"timeout_seconds": timeout_seconds},
                    cause=exc,
                ) from exc
            except FileNotFoundError as exc:
                raise TtsUnavailableError(
                    "FFmpeg executable was not found for flite narration",
                    cause=exc,
                ) from exc
            if result.returncode != 0:
                raise TtsUnavailableError(
                    "FFmpeg flite narration failed; this FFmpeg build may lack "
                    "the flite filter or the voice may not support the text",
                    details={
                        "exit_code": result.returncode,
                        "stderr_tail": (result.stderr or "").strip().splitlines()[-8:],
                    },
                )
        _validate_wav(output_path)


class AutoNarrator:
    """Prefer Windows SAPI, fall back to FFmpeg flite."""

    name = "auto_local"

    def __init__(self, *, ffmpeg_path: str = "ffmpeg", rate: int = 0) -> None:
        self.sapi = WindowsSapiNarrator(rate=rate)
        self.flite = FliteNarrator(ffmpeg_path=ffmpeg_path)
        self._selected: NarrationSynthesizer | None = None

    def _select(self) -> NarrationSynthesizer:
        if self._selected is not None:
            return self._selected
        if self.sapi.available():
            self._selected = self.sapi
        elif self.flite.available():
            self._selected = self.flite
        else:
            raise TtsUnavailableError(
                "No local narration synthesizer is available. On Windows, enable "
                "System.Speech PowerShell support; otherwise install an FFmpeg "
                "build with the flite filter."
            )
        return self._selected

    def available(self) -> bool:
        return self.sapi.available() or self.flite.available()

    @property
    def selected_name(self) -> str | None:
        try:
            return self._select().name
        except TtsUnavailableError:
            return None

    def synthesize(
        self, text: str, output_path: Path, *, timeout_seconds: float = 60.0
    ) -> None:
        self._select().synthesize(
            text, output_path, timeout_seconds=timeout_seconds
        )
