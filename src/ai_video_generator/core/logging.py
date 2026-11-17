"""Structured application logging.

Logs include only operational context (job/scene/provider identifiers). Secret
keys and values are redacted defensively, and callers must not pass narration or
provider payloads as log fields.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, IO

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
_RESERVED = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SECRET_MARKERS)


def _sanitize(value: Any, depth: int = 0) -> Any:
    if depth > 3:
        return "<truncated>"
    if isinstance(value, dict):
        return {
            key: "<redacted>" if _is_secret_key(str(key)) else _sanitize(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, depth + 1) for item in value[:20]]
    if isinstance(value, str) and len(value) > 500:
        return value[:497] + "..."
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = _sanitize(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class PlainFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        context = {
            key: _sanitize(value)
            for key, value in record.__dict__.items()
            if key not in _RESERVED and not key.startswith("_")
        }
        suffix = ""
        if context:
            suffix = " " + " ".join(f"{key}={value}" for key, value in context.items())
        return f"{self.formatTime(record)} {record.levelname} {record.name}: {record.getMessage()}{suffix}"


def configure_logging(
    level: str = "INFO",
    *,
    json_logs: bool = True,
    stream: IO[str] | None = None,
) -> None:
    """Configure the root logger once.

    Repeated calls replace only the handler installed by this function; they do
    not disable or mutate unrelated handlers.
    """

    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Unsupported log level: {level!r}")

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_aivg_handler", False):
            root.removeHandler(handler)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler._aivg_handler = True  # type: ignore[attr-defined]
    handler.setFormatter(JsonFormatter() if json_logs else PlainFormatter())
    root.addHandler(handler)
    root.setLevel(numeric_level)


def get_logger(name: str, **context: Any) -> logging.LoggerAdapter:
    """Return a logger adapter with sanitized structured context."""

    return logging.LoggerAdapter(logging.getLogger(name), _sanitize(context))
