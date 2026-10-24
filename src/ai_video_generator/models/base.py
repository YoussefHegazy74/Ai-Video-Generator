"""Shared primitives for the scene-planning contracts.

Responsibility: provide dependency-free contract helpers used by the Script
Agent, the Whiteboard Director Agent, and their shared schemas.
Workflow: shared scene-planning layer; no provider calls or workflow logic.
Inputs: Python values and dictionaries produced by upstream planning or an LLM.
Outputs: validated values, serializable contract objects, and ``ContractError``
failures with precise field paths.
Interacts with: ``models.source``, ``models.lesson_plan``, ``models.scene``,
the whiteboard contract modules, and agent output parsers.
"""

from __future__ import annotations

import dataclasses
import json
import math
import re
from enum import Enum
from typing import Any, Mapping, Sequence

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ContractError(ValueError):
    """Raised when a scene-planning contract is structurally invalid."""


class ContractModel:
    """Mixin that gives frozen contract dataclasses stable JSON serialization."""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible dictionary representation."""
        return _to_jsonable(dataclasses.asdict(self))

    def to_json(self) -> str:
        """Return a stable JSON string representation."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return {
            field.name: _to_jsonable(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value


def ensure_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{field} must be an object")
    return value


def ensure_sequence(value: Any, field: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(
        value, (list, tuple)
    ):
        raise ContractError(f"{field} must be a list")
    return value


def require_keys(
    data: Any,
    *,
    allowed: set[str],
    required: set[str],
    field: str = "object",
) -> Mapping[str, Any]:
    mapping = ensure_mapping(data, field)
    unknown = sorted(set(mapping) - allowed)
    missing = sorted(required - set(mapping))
    if unknown:
        raise ContractError(f"{field} contains unknown fields: {', '.join(unknown)}")
    if missing:
        raise ContractError(f"{field} is missing required fields: {', '.join(missing)}")
    return mapping


def require_text(
    value: Any,
    field: str,
    *,
    max_length: int | None = None,
    allow_empty: bool = False,
) -> str:
    """Validate text without altering it.

    Educational text, equations, code, source excerpts, and identifiers must
    survive serialization byte-for-byte. Earlier versions stripped surrounding
    whitespace here, which silently changed narration and code when parsing
    provider output. Emptiness is still tested against a stripped copy so
    whitespace-only values remain invalid.
    """
    if not isinstance(value, str):
        raise ContractError(f"{field} must be a string")
    if not allow_empty and not value.strip():
        raise ContractError(f"{field} must not be empty")
    if max_length is not None and len(value) > max_length:
        raise ContractError(f"{field} must be at most {max_length} characters")
    return value


def optional_text(value: Any, field: str, *, max_length: int | None = None) -> str | None:
    if value is None:
        return None
    return require_text(value, field, max_length=max_length)


def require_id(value: Any, field: str) -> str:
    identifier = require_text(value, field, max_length=128)
    if not _ID_PATTERN.fullmatch(identifier):
        raise ContractError(
            f"{field} must match {_ID_PATTERN.pattern} (letters, digits, '.', '_', ':', '-')"
        )
    return identifier


def require_id_tuple(
    value: Any,
    field: str,
    *,
    min_items: int = 0,
    max_items: int | None = None,
) -> tuple[str, ...]:
    """Validate a sequence of identifiers without normalizing them."""
    items = ensure_sequence(value, field)
    if len(items) < min_items:
        raise ContractError(f"{field} must contain at least {min_items} item(s)")
    if max_items is not None and len(items) > max_items:
        raise ContractError(f"{field} must contain at most {max_items} item(s)")
    return tuple(
        require_id(item, f"{field}[{index}]") for index, item in enumerate(items)
    )


def require_text_tuple(
    value: Any,
    field: str,
    *,
    min_items: int = 0,
    max_items: int | None = None,
    max_length: int | None = None,
) -> tuple[str, ...]:
    items = ensure_sequence(value, field)
    if len(items) < min_items:
        raise ContractError(f"{field} must contain at least {min_items} item(s)")
    if max_items is not None and len(items) > max_items:
        raise ContractError(f"{field} must contain at most {max_items} item(s)")
    return tuple(
        require_text(item, f"{field}[{index}]", max_length=max_length)
        for index, item in enumerate(items)
    )


def require_int(
    value: Any,
    field: str,
    *,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{field} must be an integer")
    if min_value is not None and value < min_value:
        raise ContractError(f"{field} must be at least {min_value}")
    if max_value is not None and value > max_value:
        raise ContractError(f"{field} must be at most {max_value}")
    return value


def require_number(
    value: Any,
    field: str,
    *,
    min_value: float | None = None,
    max_value: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{field} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ContractError(f"{field} must be finite")
    if min_value is not None and number < min_value:
        raise ContractError(f"{field} must be at least {min_value}")
    if max_value is not None and number > max_value:
        raise ContractError(f"{field} must be at most {max_value}")
    return number


def optional_number(
    value: Any,
    field: str,
    *,
    min_value: float | None = None,
    max_value: float | None = None,
) -> float | None:
    if value is None:
        return None
    return require_number(value, field, min_value=min_value, max_value=max_value)


def require_enum(value: Any, enum_type: type[Enum], field: str) -> Any:
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError as exc:
            allowed = ", ".join(item.value for item in enum_type)
            raise ContractError(f"{field} must be one of: {allowed}") from exc
    raise ContractError(f"{field} must be a string enum value")


def require_unique(values: Sequence[Any], field: str) -> None:
    seen: set[Any] = set()
    duplicates: set[Any] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        formatted = ", ".join(sorted(str(item) for item in duplicates))
        raise ContractError(f"{field} contains duplicate value(s): {formatted}")


def require_strictly_increasing(values: Sequence[int], field: str) -> None:
    if any(left >= right for left, right in zip(values, values[1:])):
        raise ContractError(f"{field} must be strictly increasing")
