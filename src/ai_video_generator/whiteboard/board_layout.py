"""Whiteboard board-layout contract.

Responsibility: describe normalized board regions, their reading order, and
spatial bounds for one scene.
Workflow: produced by the Whiteboard Director Agent and consumed by drawing,
handwriting, highlighting, validation, and scene packaging.
Inputs: region identifiers, region kinds, normalized coordinates, and labels.
Outputs: validated ``BoardLayout`` and ``BoardRegion`` objects.
Interacts with: ``whiteboard.drawing_instructions``, ``whiteboard.plan``,
validation, and future renderers. Coordinates are normalized to the inclusive
range 0.0-1.0 so no resolution is assumed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..models.base import (
    ContractError,
    ContractModel,
    ensure_sequence,
    optional_text,
    require_enum,
    require_id,
    require_keys,
    require_number,
    require_unique,
)

# Shared with the provider-facing JSON Schema.
MIN_REGION_DIMENSION = 0.001
MAX_BOARD_REGIONS = 12


class BoardRegionKind(str, Enum):
    """Semantic role of a whiteboard region."""

    TITLE = "title"
    CONTENT = "content"
    EXAMPLE = "example"
    DIAGRAM = "diagram"
    CODE = "code"
    EQUATION = "equation"
    SUMMARY = "summary"


@dataclass(frozen=True, slots=True)
class BoardRegion(ContractModel):
    """A rectangular region in normalized board coordinates."""

    region_id: str
    kind: BoardRegionKind
    x: float
    y: float
    width: float
    height: float
    label: str | None = None

    def __post_init__(self) -> None:
        require_id(self.region_id, "region.region_id")
        if not isinstance(self.kind, BoardRegionKind):
            object.__setattr__(
                self,
                "kind",
                require_enum(self.kind, BoardRegionKind, "region.kind"),
            )
        x = require_number(self.x, "region.x", min_value=0.0, max_value=1.0)
        y = require_number(self.y, "region.y", min_value=0.0, max_value=1.0)
        width = require_number(
            self.width,
            "region.width",
            min_value=MIN_REGION_DIMENSION,
            max_value=1.0,
        )
        height = require_number(
            self.height,
            "region.height",
            min_value=MIN_REGION_DIMENSION,
            max_value=1.0,
        )
        if x + width > 1.000001:
            raise ContractError("region.x + region.width must not exceed 1.0")
        if y + height > 1.000001:
            raise ContractError("region.y + region.height must not exceed 1.0")
        optional_text(self.label, "region.label", max_length=200)

    def overlaps(self, other: "BoardRegion", *, epsilon: float = 1e-6) -> bool:
        """Return whether two regions overlap by more than ``epsilon``."""
        if not isinstance(other, BoardRegion):
            raise ContractError("overlaps() requires another BoardRegion")
        separated = (
            self.x + self.width <= other.x + epsilon
            or other.x + other.width <= self.x + epsilon
            or self.y + self.height <= other.y + epsilon
            or other.y + other.height <= self.y + epsilon
        )
        return not separated

    @classmethod
    def from_dict(cls, data: Any) -> "BoardRegion":
        mapping = require_keys(
            data,
            allowed={
                "region_id",
                "kind",
                "x",
                "y",
                "width",
                "height",
                "label",
            },
            required={"region_id", "kind", "x", "y", "width", "height"},
            field="region",
        )
        return cls(
            region_id=require_id(mapping["region_id"], "region.region_id"),
            kind=require_enum(mapping["kind"], BoardRegionKind, "region.kind"),
            x=require_number(mapping["x"], "region.x", min_value=0.0, max_value=1.0),
            y=require_number(mapping["y"], "region.y", min_value=0.0, max_value=1.0),
            width=require_number(
                mapping["width"],
                "region.width",
                min_value=MIN_REGION_DIMENSION,
                max_value=1.0,
            ),
            height=require_number(
                mapping["height"],
                "region.height",
                min_value=MIN_REGION_DIMENSION,
                max_value=1.0,
            ),
            label=optional_text(mapping.get("label"), "region.label", max_length=200),
        )


@dataclass(frozen=True, slots=True)
class BoardLayout(ContractModel):
    """Ordered, non-empty set of board regions for one scene."""

    regions: tuple[BoardRegion, ...]

    def __post_init__(self) -> None:
        regions = ensure_sequence(self.regions, "layout.regions")
        if not regions:
            raise ContractError("layout.regions must contain at least one region")
        if len(regions) > MAX_BOARD_REGIONS:
            raise ContractError(
                f"layout.regions must contain at most {MAX_BOARD_REGIONS} regions"
            )
        if not all(isinstance(region, BoardRegion) for region in regions):
            raise ContractError("layout.regions must contain BoardRegion objects")
        object.__setattr__(self, "regions", tuple(regions))
        require_unique(
            [region.region_id for region in self.regions], "layout.regions.region_id"
        )

    def region_by_id(self, region_id: str) -> BoardRegion:
        for region in self.regions:
            if region.region_id == region_id:
                return region
        raise ContractError(f"layout does not contain region_id '{region_id}'")

    @property
    def region_ids(self) -> tuple[str, ...]:
        return tuple(region.region_id for region in self.regions)

    def overlapping_pairs(self) -> tuple[tuple[str, str], ...]:
        """Return region pairs that overlap; validation reports these as warnings."""
        pairs: list[tuple[str, str]] = []
        for index, region in enumerate(self.regions):
            for other in self.regions[index + 1 :]:
                if region.overlaps(other):
                    pairs.append((region.region_id, other.region_id))
        return tuple(pairs)

    @classmethod
    def from_dict(cls, data: Any) -> "BoardLayout":
        mapping = require_keys(
            data,
            allowed={"regions"},
            required={"regions"},
            field="layout",
        )
        region_items = ensure_sequence(mapping["regions"], "layout.regions")
        return cls(regions=tuple(BoardRegion.from_dict(item) for item in region_items))


__all__ = [
    "BoardLayout",
    "BoardRegion",
    "BoardRegionKind",
    "MAX_BOARD_REGIONS",
    "MIN_REGION_DIMENSION",
]
