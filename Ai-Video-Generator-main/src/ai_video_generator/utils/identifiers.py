"""Identifier and safe path-component helpers."""

from __future__ import annotations

import re
import uuid

from ai_video_generator.core.exceptions import SceneContractError, StoragePathError

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def new_id(prefix: str) -> str:
    """Return a non-guessable identifier with a validated prefix."""

    if not _IDENTIFIER_RE.fullmatch(prefix):
        raise ValueError(f"Invalid identifier prefix: {prefix!r}")
    return f"{prefix}_{uuid.uuid4().hex}"


def is_valid_identifier(value: str) -> bool:
    return bool(_IDENTIFIER_RE.fullmatch(value))


def require_identifier(value: str, *, field: str = "identifier") -> str:
    if not is_valid_identifier(value):
        raise SceneContractError(
            f"Invalid {field} {value!r}. Use 1-64 characters: letters, digits, "
            "dot, underscore, or hyphen; the first character must be alphanumeric.",
            details={"field": field, "value": value},
        )
    return value


def safe_component(value: str, *, field: str = "path component") -> str:
    """Validate a string that will become one filesystem path component."""

    if not _IDENTIFIER_RE.fullmatch(value):
        raise StoragePathError(
            f"Unsafe {field}: {value!r}",
            details={"field": field, "value": value},
        )
    return value
