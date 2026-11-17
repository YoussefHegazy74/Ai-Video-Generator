#!/usr/bin/env python
"""Read-only environment and capability check for the Parts 5-7 pipeline.

This script does not write files, call paid APIs, or need credentials. It reports
what is actually installed and detected, including unavailable local TTS voices
and the optional Gemini provider credential state.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_video_generator.core.settings import load_settings  # noqa: E402
from ai_video_generator.integrations.narration_synthesizer import (  # noqa: E402
    FliteNarrator,
    WindowsSapiNarrator,
)


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _run_tool(args: list[str], timeout: float = 15.0) -> subprocess.CompletedProcess[str] | None:
    executable = shutil.which(args[0])
    if executable is None:
        return None
    try:
        return subprocess.run(
            [executable, *args[1:]],
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _media_report(settings: Any) -> dict[str, Any]:
    ffmpeg_path = shutil.which(settings.media.ffmpeg_path) or (
        str(Path(settings.media.ffmpeg_path))
        if Path(settings.media.ffmpeg_path).is_file()
        else None
    )
    ffprobe_path = shutil.which(settings.media.ffprobe_path) or (
        str(Path(settings.media.ffprobe_path))
        if Path(settings.media.ffprobe_path).is_file()
        else None
    )
    ffmpeg_version = None
    ffprobe_version = None
    filters: list[str] = []
    encoders: list[str] = []
    if ffmpeg_path:
        version_result = _run_tool([ffmpeg_path, "-version"])
        if version_result and version_result.returncode == 0:
            ffmpeg_version = (version_result.stdout or "").splitlines()[0]
        filter_result = _run_tool([ffmpeg_path, "-hide_banner", "-filters"])
        if filter_result and filter_result.returncode == 0:
            filters = [
                line.split()[1]
                for line in filter_result.stdout.splitlines()
                if len(line.split()) >= 2
            ]
        encoder_result = _run_tool([ffmpeg_path, "-hide_banner", "-encoders"])
        if encoder_result and encoder_result.returncode == 0:
            encoders = [
                line.split()[1]
                for line in encoder_result.stdout.splitlines()
                if len(line.split()) >= 2
            ]
    if ffprobe_path:
        probe_result = _run_tool([ffprobe_path, "-version"])
        if probe_result and probe_result.returncode == 0:
            ffprobe_version = (probe_result.stdout or "").splitlines()[0]
    return {
        "ffmpeg_path": ffmpeg_path,
        "ffprobe_path": ffprobe_path,
        "ffmpeg_version": ffmpeg_version,
        "ffprobe_version": ffprobe_version,
        "libx264_encoder": "libx264" in encoders,
        "aac_encoder": "aac" in encoders,
        "flite_filter": "flite" in filters,
        "concat_filter": "concat" in filters,
        "overlay_filter": "overlay" in filters,
        "fade_filter": "fade" in filters,
        "silencedetect_filter": "silencedetect" in filters,
        "blackdetect_filter": "blackdetect" in filters,
        "drawtext_filter": "drawtext" in filters,
    }


def _tts_report(settings: Any) -> dict[str, Any]:
    sapi = WindowsSapiNarrator(rate=settings.provider.whiteboard.tts_rate)
    flite = FliteNarrator(ffmpeg_path=settings.media.ffmpeg_path)
    return {
        "windows_sapi_available": sapi.available(),
        "ffmpeg_flite_executable_available": flite.available(),
        "configured_narrator": settings.provider.whiteboard.narrator,
    }


def build_report(config_dir: str | None) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    packages = {
        name: _package_version(name)
        for name in (
            "pydantic",
            "Pillow",
            "PyYAML",
            "python-dotenv",
            "google-genai",
        )
    }
    report: dict[str, Any] = {
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
            "platform": platform.platform(),
        },
        "packages": packages,
        "settings": None,
        "media": None,
        "tts": None,
        "provider": None,
        "ok": False,
    }
    try:
        settings = load_settings(config_dir=config_dir)
    except Exception as exc:
        report["settings"] = {
            "loaded": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        warnings.append("Configuration could not be loaded.")
        return report, warnings

    report["settings"] = {
        "loaded": True,
        "environment": settings.environment,
        "project_root": str(settings.project_root),
        "storage_root": str(settings.storage_root),
        "temp_root": str(settings.temp_root),
        "registry_path": str(settings.registry_path),
        "provider": settings.provider.id,
        "validation_min_scene_duration_seconds": (
            settings.validation.min_scene_duration_seconds
        ),
        "validation_max_scene_duration_seconds": (
            settings.validation.max_scene_duration_seconds
        ),
        "assembly_output": {
            "width": settings.assembly.width,
            "height": settings.assembly.height,
            "fps": settings.assembly.fps,
            "transition": settings.assembly.transition.value,
        },
    }
    media = _media_report(settings)
    report["media"] = media
    report["tts"] = _tts_report(settings)
    report["provider"] = {
        "selected": settings.provider.id,
        "gemini_model": settings.provider.gemini.model,
        "gemini_api_key_configured": bool(os.environ.get("GEMINI_API_KEY")),
        "google_genai_installed": packages.get("google-genai") is not None,
        "gemini_live_call_tested": False,
        "gemini_duration_control": False,
    }

    if not media["ffmpeg_path"]:
        warnings.append("ffmpeg was not found on PATH.")
    if not media["ffprobe_path"]:
        warnings.append("ffprobe was not found on PATH.")
    if media["ffmpeg_path"] and not media["libx264_encoder"]:
        warnings.append("FFmpeg does not report the libx264 encoder.")
    if media["ffmpeg_path"] and not media["aac_encoder"]:
        warnings.append("FFmpeg does not report the AAC encoder.")
    if not report["tts"]["windows_sapi_available"] and not media["flite_filter"]:
        warnings.append(
            "No local TTS path detected: Windows SAPI is unavailable and the "
            "FFmpeg build has no flite filter. The deterministic provider cannot "
            "generate narration on this host."
        )
    if settings.provider.id == "gemini_omni" and not os.environ.get("GEMINI_API_KEY"):
        warnings.append(
            "gemini_omni is selected but GEMINI_API_KEY is not set. "
            "The local deterministic provider needs no credential."
        )
    report["warnings"] = warnings
    report["ok"] = bool(media["ffmpeg_path"] and media["ffprobe_path"])
    return report, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config-dir",
        default=None,
        help="Configuration directory (default: <repo>/config)",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON")
    args = parser.parse_args(argv)

    report, _warnings = build_report(args.config_dir)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        print(f"Python: {report['python']['version']} ({report['python']['platform']})")
        print("Packages:")
        for name, version in report["packages"].items():
            print(f"  {name}: {version or 'not installed'}")
        settings = report["settings"]
        print("Settings:")
        if settings and settings.get("loaded"):
            print(f"  environment: {settings['environment']}")
            print(f"  storage root: {settings['storage_root']}")
            print(f"  provider: {settings['provider']}")
        else:
            print(f"  ERROR: {settings.get('error') if settings else 'unknown'}")
        media = report["media"] or {}
        print("Media tools:")
        print(f"  ffmpeg: {media.get('ffmpeg_version') or 'not found'}")
        print(f"  ffprobe: {media.get('ffprobe_version') or 'not found'}")
        print(f"  libx264: {media.get('libx264_encoder')}")
        print(f"  aac: {media.get('aac_encoder')}")
        print(f"  flite filter: {media.get('flite_filter')}")
        print("Local TTS:")
        print(f"  Windows SAPI: {report['tts']['windows_sapi_available']}")
        print(f"  FFmpeg flite: {media.get('flite_filter')}")
        print("Providers:")
        provider = report["provider"]
        print(f"  selected: {provider['selected']}")
        print(f"  GEMINI_API_KEY configured: {provider['gemini_api_key_configured']}")
        print(f"  google-genai installed: {provider['google_genai_installed']}")
        print("  Gemini live call: not tested (requires credentials and paid usage)")
        if report.get("warnings"):
            print("Warnings:")
            for warning in report["warnings"]:
                print(f"  - {warning}")
        print(f"Overall media prerequisites: {'OK' if report['ok'] else 'MISSING'}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
