"""Excerpt editions: content and annotations read through spans of one stored edition."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, LiteralString

from database import span as span_database
from database.content_state import read_state, read_value
from database.locking import lock_nodes
from database.repository import fetch_all
from database.span import adjust_annotation_span
from errors import DataConflictError, DataValidationError
from models.annotation import (
    BibliographicMetadataOutput,
    MarkOutput,
    NoteOutput,
    SegmentOutput,
    Span,
    TableOfContentsOutput,
    TableOfContentsSectionOutput,
)

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction

    from models.edition import ExcerptSource


@dataclass(frozen=True)
class ExcerptSlice:
    """A span of the source edition and the position where it starts in the excerpt edition."""

    index: int
    edition_id: str
    key: str
    start: int
    end: int
    offset: int
    last: bool

    def to_local(self, position: int) -> int:
        return self.offset + min(max(position, self.start), self.end) - self.start

    def to_source(self, span: Span) -> Span | None:
        """The part of a range of excerpt edition positions that falls in this span, in source positions."""
        start, end = (max(span.start, self.offset), min(span.end, self.offset + self.end - self.start))
        if start < end:
            return Span(start=self.start + start - self.offset, end=self.start + end - self.offset)
        return None

    def project(self, span: Span, *, whole: bool = False) -> Span | None:
        """Trim and shift a source span; `whole` keeps only spans fully inside. A point at the end goes to the last."""
        if span.start == span.end:
            inside = self.start <= span.start < self.end or (self.last and span.start == self.end)
        elif whole:
            inside = self.start <= span.start and span.end <= self.end
        else:
            inside = span.start < self.end and span.end > self.start
        return Span(start=self.to_local(span.start), end=self.to_local(span.end)) if inside else None

    def project_sections(self, sections: list[TableOfContentsSectionOutput]) -> list[TableOfContentsSectionOutput]:
        return [
            section.model_copy(update={"span": span, "subsections": self.project_sections(section.subsections)})
            for section in sections
            if (span := self.project(section.span))
        ]

    def project_segments(self, segments: list[SegmentOutput]) -> list[SegmentOutput]:
        projected = ((segment, [line for line in map(self.project, segment.lines) if line]) for segment in segments)
        return [segment.model_copy(update={"lines": lines}) for segment, lines in projected if lines]


@dataclass(frozen=True)
class ExcerptMap:
    """Maps the source edition's annotations into an excerpt edition: trimmed, shifted and owned by it."""

    edition_id: str
    text_id: str
    slices: list[ExcerptSlice]

    @property
    def owner(self) -> dict[str, str]:
        return {"edition_id": self.edition_id, "text_id": self.text_id}

    def project_annotations[T: (NoteOutput, MarkOutput, BibliographicMetadataOutput)](
        self, annotations: list[T], *, whole: bool = False
    ) -> list[T]:
        return [
            annotation.model_copy(update=self.owner | {"span": span})
            for piece in self.slices
            for annotation in annotations
            if (span := piece.project(annotation.span, whole=whole))
        ]

    def project_tocs(self, tocs: list[TableOfContentsOutput]) -> list[TableOfContentsOutput]:
        projected = ((toc, [s for piece in self.slices for s in piece.project_sections(toc.sections)]) for toc in tocs)
        return [toc.model_copy(update=self.owner | {"sections": sections}) for toc, sections in projected if sections]


type EditionReader[T] = Callable[[AsyncManagedTransaction, str, ExcerptMap | None], Awaitable[T]]


async def read_edition[T](tx: AsyncManagedTransaction, edition_id: str, read: EditionReader[T]) -> T:
    """Run `read` on the edition, or for an excerpt edition on its source with the map to project the result."""
    state = await read_state(tx, edition_id)
    if not state.is_excerpt:
        return await read_value(tx, edition_id, lambda tx: read(tx, edition_id, None))

    async def through_source(tx: AsyncManagedTransaction) -> T:
        slices = await get_slices(tx, edition_id)
        excerpt = ExcerptMap(edition_id, state.text_id, slices)
        return await read_value(tx, slices[0].edition_id, lambda tx: read(tx, slices[0].edition_id, excerpt))

    return await read_value(tx, edition_id, through_source)


async def read_annotations[T: (NoteOutput, MarkOutput, BibliographicMetadataOutput)](
    tx: AsyncManagedTransaction,
    edition_id: str,
    query: LiteralString,
    model: type[T],
    *,
    whole: bool = False,
    **params: str,
) -> list[T]:
    """Single-span annotations of the edition; an excerpt edition gets its source's, projected by `whole`."""

    async def read(tx: AsyncManagedTransaction, source_id: str, excerpt: ExcerptMap | None) -> list[T]:
        annotations = await fetch_all(tx, query, model, edition_id=source_id, **params)
        return excerpt.project_annotations(annotations, whole=whole) if excerpt else annotations

    return await read_edition(tx, edition_id, read)


CREATE_QUERY: LiteralString = """
MATCH (edition:Edition {id: $edition_id}), (source:Edition {id: $source_id})
CREATE (edition)-[:EXCERPT_OF]->(source)
WITH edition
UNWIND $spans AS s
CREATE (:Span {start: s.start, end: s.end, index: s.index})-[:SPAN_OF]->(edition)
FINISH
"""
GET_WHOLE_QUERY: LiteralString = """
MATCH (:Text {id: $text_id})-[:PART_OF]->(whole:Text) RETURN whole.id AS id
"""
GET_SLICES_QUERY: LiteralString = """
MATCH (edition:Edition {id: $edition_id})-[:EXCERPT_OF]->(source:Edition), (span:Span)-[:SPAN_OF]->(edition)
RETURN span.index AS index, source.id AS edition_id, source.content_key AS key, span.start AS start, span.end AS end
ORDER BY span.index
"""
EXCERPT_EDITIONS_OF_QUERY: LiteralString = """
MATCH (:Edition {id: $edition_id})<-[:EXCERPT_OF]-(edition:Edition) RETURN edition.id AS id ORDER BY id
"""
UPDATE_AFTER_EDIT_QUERY: LiteralString = """
MATCH (edition:Edition {id: $edition_id})
SET edition.content_length = edition.content_length + $delta
WITH edition
UNWIND $updates AS u
MATCH (span:Span {index: u.index})-[:SPAN_OF]->(edition)
SET span.start = u.start, span.end = u.end
FINISH
"""
DELETE_QUERY: LiteralString = """
MATCH (span:Span)-[:SPAN_OF]->(:Edition {id: $edition_id})
DETACH DELETE span
FINISH
"""


async def add(tx: AsyncManagedTransaction, edition_id: str, text_id: str, excerpt_of: ExcerptSource) -> None:
    """Spans of an excerpt edition are stored as the matching spans of the edition holding the content."""
    source_id = excerpt_of.edition_id
    if (await read_state(tx, source_id)).is_excerpt:
        source_id = (await get_slices(tx, source_id))[0].edition_id
    await lock_nodes(tx, "Edition", [source_id])
    named = await read_state(tx, excerpt_of.edition_id)
    source = await read_state(tx, source_id) if named.is_excerpt else named
    if text_id in (named.text_id, source.text_id):
        raise DataValidationError("An excerpt edition must belong to another text than the edition it excerpts")
    whole = await (await tx.run(GET_WHOLE_QUERY, text_id=text_id)).single()
    if whole is not None and named.text_id != whole["id"]:
        raise DataValidationError(f"Edition '{named.edition_id}' is not an edition of '{whole['id']}', the whole text")
    named.validate_span(max(span.end for span in excerpt_of.spans))
    ranges = excerpt_of.spans
    if named.is_excerpt:
        slices = await get_slices(tx, named.edition_id)
        ranges = [source_span for span in ranges for piece in slices if (source_span := piece.to_source(span))]
    spans = [{"index": i, "start": span.start, "end": span.end} for i, span in enumerate(ranges)]
    result = await tx.run(CREATE_QUERY, edition_id=edition_id, source_id=source_id, spans=spans)
    await result.consume()


async def get_slices(tx: AsyncManagedTransaction, edition_id: str) -> list[ExcerptSlice]:
    records = await (await tx.run(GET_SLICES_QUERY, edition_id=edition_id)).data()
    slices, offset = ([], 0)
    for record in records:
        slices.append(ExcerptSlice(**record, offset=offset, last=record is records[-1]))
        offset += record["end"] - record["start"]
    return slices


async def excerpt_editions_of(tx: AsyncManagedTransaction, edition_id: str) -> list[str]:
    result = await tx.run(EXCERPT_EDITIONS_OF_QUERY, edition_id=edition_id)
    return await result.value("id")


async def propagate_edit(tx: AsyncManagedTransaction, source_id: str, start: int, end: int, new_len: int) -> None:
    """Replay a source content edit into each excerpt edition using it; the caller locks those editions."""
    for edition_id in await excerpt_editions_of(tx, source_id):
        edits: list[tuple[int, int, int]] = []
        updates: list[dict[str, int]] = []
        delta = 0
        for piece in await get_slices(tx, edition_id):
            adjusted = adjust_annotation_span(piece.start, piece.end, start, end, new_len)
            if adjusted is None:
                raise DataConflictError(f"The edit would remove a span of excerpt edition '{edition_id}'")
            growth = adjusted[1] - adjusted[0] - (piece.end - piece.start)
            local_start, local_end = (piece.to_local(start), piece.to_local(end))
            if growth or local_start != local_end:
                edits.append((local_start, local_end, local_end - local_start + growth))
            if adjusted != (piece.start, piece.end):
                span = Span(start=adjusted[0], end=adjusted[1])
                updates.append({"index": piece.index, "start": span.start, "end": span.end})
            delta += growth
        for local_start, local_end, inserted in reversed(edits):
            await span_database.adjust_spans(tx, edition_id, local_start, local_end, inserted)
        if updates:
            await tx.run(UPDATE_AFTER_EDIT_QUERY, edition_id=edition_id, delta=delta, updates=updates)
