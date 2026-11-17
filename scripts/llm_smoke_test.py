#!/usr/bin/env python
"""One-request live smoke test for the configured structured-output LLM.

This command:
  1. loads the application settings,
  2. constructs the Gemini LLM adapter from configuration,
  3. sends one minimal JSON-schema request,
  4. validates the returned JSON object,
  5. prints a concise result.

It does not run a lesson, call the video pipeline, or write artifacts. It is
never part of the automated test suite. A real request requires ``GEMINI_API_KEY``
and the optional ``google-genai`` package; this may incur provider charges.

Usage:
    export GEMINI_API_KEY=...
    python scripts/llm_smoke_test.py
    python scripts/llm_smoke_test.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_video_generator.agents.scene_planning.errors import (  # noqa: E402
    ScenePlanningError,
)
from ai_video_generator.agents.scene_planning.llm import (  # noqa: E402
    LLMRequest,
    parse_json_object,
)
from ai_video_generator.core.exceptions import AppError  # noqa: E402
from ai_video_generator.core.logging import configure_logging, get_logger  # noqa: E402
from ai_video_generator.core.settings import load_settings  # noqa: E402
from ai_video_generator.integrations.gemini_llm_client import (  # noqa: E402
    GeminiLLMClient,
)

logger = get_logger(__name__)

SMOKE_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ok"]},
        "message": {"type": "string"},
    },
    "required": ["status", "message"],
}
SMOKE_SYSTEM_PROMPT = (
    "You are a connectivity health check. Reply only with the requested JSON "
    "object."
)
SMOKE_USER_PROMPT = (
    'Return JSON with "status" set to "ok" and "message" set to one short '
    "sentence confirming structured output works."
)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config-dir",
        default=None,
        help="Configuration directory (default: <repo>/config)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override the configured LLM model for this smoke request",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print a single JSON object"
    )
    parser.add_argument(
        "--plain-logs", action="store_true", help="Disable JSON logs"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        settings = load_settings(config_dir=args.config_dir)
        configure_logging(settings.log_level, json_logs=not args.plain_logs)
        api_key = (
            settings.provider.gemini_api_key.get_secret_value()
            if settings.provider.gemini_api_key is not None
            else None
        )
        client = GeminiLLMClient(
            api_key=api_key,
            model=args.model or settings.llm.model,
            max_attempts=settings.llm.max_attempts,
            timeout_seconds=settings.llm.timeout_seconds,
            max_output_tokens=min(settings.llm.max_output_tokens, 1024),
            backoff_base_seconds=settings.llm.backoff_base_seconds,
            backoff_max_seconds=settings.llm.backoff_max_seconds,
            backoff_jitter_seconds=settings.llm.backoff_jitter_seconds,
        )
        started = time.monotonic()
        response = client.generate(
            LLMRequest(
                system_prompt=SMOKE_SYSTEM_PROMPT,
                user_prompt=SMOKE_USER_PROMPT,
                response_schema_name="LLMSmokeResult",
                response_schema=SMOKE_SCHEMA,
                temperature=0.0,
                metadata={"agent": "llm_smoke_test"},
            )
        )
        payload = parse_json_object(response.content)
        if payload.get("status") != "ok" or not payload.get("message"):
            raise ValueError(
                "provider returned JSON that does not satisfy the smoke-test "
                "expectations"
            )
        result = {
            "status": "ok",
            "provider": response.provider,
            "model": response.model,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "message": payload["message"],
        }
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(
                f"LLM smoke test passed: provider={result['provider']} "
                f"model={result['model']} latency={result['latency_ms']}ms"
            )
            print(f"model message: {result['message']}")
        return 0
    except AppError as exc:
        result = {
            "status": "error",
            "error_code": exc.code,
            "message": exc.message,
            "details": exc.details,
        }
        print(
            json.dumps(result, indent=2, ensure_ascii=False, default=str)
            if not args.json
            else json.dumps(result, ensure_ascii=False, default=str)
        )
        logger.error(
            "LLM smoke test failed",
            extra={"error_code": exc.code, "error_details": exc.details},
        )
        return 1
    except (ScenePlanningError, ValueError, OSError) as exc:
        result = {
            "status": "error",
            "error_type": type(exc).__name__,
            "message": str(exc),
            "details": list(getattr(exc, "details", ())),
        }
        print(json.dumps(result, ensure_ascii=False, default=str))
        logger.error(
            "LLM smoke test failed",
            extra={"error_type": type(exc).__name__},
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
