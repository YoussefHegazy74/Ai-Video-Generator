"""Scene timing helpers shared by generation and validation."""

from __future__ import annotations


def closest_supported_duration(
    requested_seconds: float,
    supported_seconds: list[float] | tuple[float, ...] | None,
) -> float | None:
    """Map a target to the nearest explicitly supported provider duration."""

    if requested_seconds <= 0:
        raise ValueError("requested_seconds must be positive")
    if not supported_seconds:
        return None
    return min(
        (float(value) for value in supported_seconds),
        key=lambda value: (abs(value - requested_seconds), value),
    )


def within_duration_range(
    value_seconds: float,
    minimum_seconds: float,
    maximum_seconds: float,
    *,
    tolerance_seconds: float = 0.0,
) -> bool:
    if minimum_seconds > maximum_seconds:
        raise ValueError("minimum_seconds must not exceed maximum_seconds")
    if tolerance_seconds < 0:
        raise ValueError("tolerance_seconds must be non-negative")
    return (
        minimum_seconds - tolerance_seconds
        <= value_seconds
        <= maximum_seconds + tolerance_seconds
    )
