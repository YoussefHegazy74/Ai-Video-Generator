"""Source provenance contracts used by scene planning.

Responsibility: preserve the document, page range, and source-excerpt context
that the Script Agent and Whiteboard Director Agent must respect.
Workflow: produced by ingestion/planning, carried through both scene-planning
agents, and retained in the final scene-planning artifact.
Inputs: document identifiers, one-based page numbers, and extracted excerpts.
Outputs: validated ``PageRange``, ``SourceReference``, and ``SourceExcerpt``
objects with stable JSON serialization.
Interacts with: ``models.lesson_plan``, ``models.scene``, prompt builders,
validation, and downstream scene packaging.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from .base import (
    ContractError,
    ContractModel,
    ensure_sequence,
    optional_text,
    require_enum,
    require_id,
    require_int,
    require_keys,
    require_text,
)


class SourceKind(str, Enum):
    """Kinds of source material that can appear in a selected page range."""

    TEXT = "text"
    FIGURE = "figure"
    EQUATION = "equation"
    CODE = "code"
    TABLE = "table"
    DIAGRAM = "diagram"


@dataclass(frozen=True, slots=True)
class PageRange(ContractModel):
    """Inclusive, one-based page range selected by the user."""

    start_page: int
    end_page: int

    def __post_init__(self) -> None:
        start = require_int(self.start_page, "page_range.start_page", min_value=1)
        end = require_int(self.end_page, "page_range.end_page", min_value=1)
        if end < start:
            raise ContractError("page_range.end_page must be >= page_range.start_page")

    def contains(self, page_number: int) -> bool:
        """Return whether the one-based page number is inside this range."""
        return self.start_page <= page_number <= self.end_page

    @property
    def label(self) -> str:
        if self.start_page == self.end_page:
            return str(self.start_page)
        return f"{self.start_page}-{self.end_page}"

    @classmethod
    def from_dict(cls, data: Any) -> "PageRange":
        mapping = require_keys(
            data,
            allowed={"start_page", "end_page"},
            required={"start_page", "end_page"},
            field="page_range",
        )
        return cls(
            start_page=require_int(mapping["start_page"], "page_range.start_page", min_value=1),
            end_page=require_int(mapping["end_page"], "page_range.end_page", min_value=1),
        )


@dataclass(frozen=True, slots=True)
class SourceReference(ContractModel):
    """A stable reference to a location in the source document.

    ``page_number`` is one-based. ``section_title`` and ``anchor`` are optional
    but should be preserved when ingestion can provide them.
    """

    document_id: str
    page_number: int
    section_title: str | None = None
    anchor: str | None = None

    def __post_init__(self) -> None:
        require_id(self.document_id, "source_reference.document_id")
        require_int(self.page_number, "source_reference.page_number", min_value=1)
        optional_text(self.section_title, "source_reference.section_title", max_length=300)
        optional_text(self.anchor, "source_reference.anchor", max_length=300)

    @property
    def identity(self) -> tuple[str, int, str, str]:
        """Return the fields that uniquely identify this source location."""
        return (
            self.document_id,
            self.page_number,
            self.section_title or "",
            self.anchor or "",
        )

    @classmethod
    def from_dict(cls, data: Any) -> "SourceReference":
        mapping = require_keys(
            data,
            allowed={"document_id", "page_number", "section_title", "anchor"},
            required={"document_id", "page_number"},
            field="source_reference",
        )
        return cls(
            document_id=require_id(mapping["document_id"], "source_reference.document_id"),
            page_number=require_int(
                mapping["page_number"], "source_reference.page_number", min_value=1
            ),
            section_title=optional_text(
                mapping.get("section_title"),
                "source_reference.section_title",
                max_length=300,
            ),
            anchor=optional_text(
                mapping.get("anchor"), "source_reference.anchor", max_length=300
            ),
        )


@dataclass(frozen=True, slots=True)
class SourceExcerpt(ContractModel):
    """A source-grounded excerpt supplied to the scene-planning agents.

    ``source_id`` is owned by upstream ingestion/planning and must remain stable
    for the life of the lesson. The agents cite this identifier rather than
    reproducing page references from model memory.
    """

    source_id: str
    reference: SourceReference
    text: str
    kind: SourceKind = SourceKind.TEXT
    caption: str | None = None

    def __post_init__(self) -> None:
        require_id(self.source_id, "source_excerpt.source_id")
        if not isinstance(self.reference, SourceReference):
            raise ContractError("source_excerpt.reference must be a SourceReference")
        require_text(self.text, "source_excerpt.text", max_length=20_000)
        if not isinstance(self.kind, SourceKind):
            object.__setattr__(
                self,
                "kind",
                require_enum(self.kind, SourceKind, "source_excerpt.kind"),
            )
        optional_text(self.caption, "source_excerpt.caption", max_length=1_000)

    @property
    def page_number(self) -> int:
        return self.reference.page_number

    @classmethod
    def from_dict(cls, data: Any) -> "SourceExcerpt":
        mapping = require_keys(
            data,
            allowed={"source_id", "reference", "text", "kind", "caption"},
            required={"source_id", "reference", "text"},
            field="source_excerpt",
        )
        reference_data = mapping["reference"]
        return cls(
            source_id=require_id(mapping["source_id"], "source_excerpt.source_id"),
            reference=SourceReference.from_dict(reference_data),
            text=require_text(mapping["text"], "source_excerpt.text", max_length=20_000),
            kind=require_enum(
                mapping.get("kind", SourceKind.TEXT.value),
                SourceKind,
                "source_excerpt.kind",
            ),
            caption=optional_text(
                mapping.get("caption"), "source_excerpt.caption", max_length=1_000
            ),
        )


def page_is_selected(page_number: int, selected_pages: tuple[PageRange, ...]) -> bool:
    """Return whether a page belongs to any selected range."""
    return any(page_range.contains(page_number) for page_range in selected_pages)


def require_source_pages_selected(
    excerpts: Sequence[SourceExcerpt],
    selected_pages: tuple[PageRange, ...],
    field: str,
) -> None:
    """Reject excerpts whose page is outside the user-selected ranges."""
    for excerpt in excerpts:
        if not page_is_selected(excerpt.page_number, selected_pages):
            raise ContractError(
                f"{field} source '{excerpt.source_id}' is on page "
                f"{excerpt.page_number}, which is outside the selected pages"
            )


def require_unique_source_ids(excerpts: Sequence[SourceExcerpt], field: str) -> None:
    """Reject duplicate source identifiers within a scene or lesson context."""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for excerpt in excerpts:
        if excerpt.source_id in seen:
            duplicates.add(excerpt.source_id)
        seen.add(excerpt.source_id)
    if duplicates:
        formatted = ", ".join(sorted(duplicates))
        raise ContractError(f"{field} contains duplicate source_id value(s): {formatted}")


def source_excerpts_from_sequence(value: Any, field: str) -> tuple[SourceExcerpt, ...]:
    """Parse and validate a sequence of source-excerpt dictionaries."""
    items = ensure_sequence(value, field)
    if not items:
        raise ContractError(f"{field} must contain at least one source excerpt")
    excerpts = tuple(SourceExcerpt.from_dict(item) for item in items)
    require_unique_source_ids(excerpts, field)
    return excerpts


def source_excerpt_to_prompt_dict(excerpt: SourceExcerpt) -> dict[str, Any]:
    """Return the source context exposed to an LLM prompt."""
    return {
        "source_id": excerpt.source_id,
        "kind": excerpt.kind.value,
        "document_id": excerpt.reference.document_id,
        "page_number": excerpt.reference.page_number,
        "section_title": excerpt.reference.section_title,
        "anchor": excerpt.reference.anchor,
        "caption": excerpt.caption,
        "text": excerpt.text,
    }


__all__ = [
    "PageRange",
    "SourceExcerpt",
    "SourceKind",
    "SourceReference",
    "page_is_selected",
    "require_source_pages_selected",
    "require_unique_source_ids",
    "source_excerpt_to_prompt_dict",
]
