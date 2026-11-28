from typing import TYPE_CHECKING, Literal, LiteralString

from errors import DataValidationError
from models.annotation import Span

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction


def _map_boundary(position: int, start: int, end: int, new_len: int, *, side: Literal["left", "right"]) -> int:
    """Map an old offset. A boundary touched by the edit attaches before or after the replacement."""
    if position < start:
        return position
    if position > end:
        return position + new_len - (end - start)
    return start if side == "left" else start + new_len


def _adjust_continuous_for_insert(start: int, end: int, insert_pos: int, insert_len: int) -> tuple[int, int]:
    # The preceding segment owns an insertion at a shared boundary. The document origin stays fixed.
    side = "left" if insert_pos == 0 else "right"
    return (
        _map_boundary(start, insert_pos, insert_pos, insert_len, side=side),
        _map_boundary(end, insert_pos, insert_pos, insert_len, side=side),
    )


def adjust_annotation_span(
    start: int, end: int, edit_start: int, edit_end: int, new_len: int, *, clip_replacement_edges: bool = False
) -> tuple[int, int] | None:
    """Map an independent annotation, removing it when its text or point is fully overwritten."""
    if start == end:
        if edit_start < start < edit_end:
            return None
        # A point follows inserted text, but stays before a replacement that starts at the point.
        side = "left" if start == edit_start < edit_end else "right"
        point = _map_boundary(start, edit_start, edit_end, new_len, side=side)
        return point, point
    if edit_start <= start and end <= edit_end:
        return None
    end_side = "left" if clip_replacement_edges or end == edit_start else "right"
    return (
        _map_boundary(start, edit_start, edit_end, new_len, side="right"),
        _map_boundary(end, edit_start, edit_end, new_len, side=end_side),
    )


def _adjust_continuous_entities(
    entities: dict[tuple[str, str, bool], list[tuple[str, int, int]]], start: int, end: int, new_len: int
) -> dict[str, tuple[int, int]]:
    if start == end:
        return {
            sid: _adjust_continuous_for_insert(left, right, start, new_len)
            for lines in entities.values()
            for sid, left, right in lines
        }
    # Split at the first fully covered entity so it owns the replacement. Otherwise split at the edit start.
    pivots: dict[str, int] = {}
    for (collection_id, _, _), lines in entities.items():
        left, right = lines[0][1], lines[-1][2]
        if start <= left < right <= end:
            pivots[collection_id] = min(left, pivots.get(collection_id, left))

    transformed: dict[str, tuple[int, int]] = {}
    for (collection_id, _, is_page), lines in entities.items():

        def boundary(position: int, pivot: int = pivots.get(collection_id, start)) -> int:
            return _map_boundary(position, start, end, new_len, side="left" if position <= pivot else "right")

        if is_page and boundary(lines[0][1]) == boundary(lines[-1][2]):
            raise DataValidationError("Content edits must leave at least one character on every page")
        for sid, left, right in lines:
            adjusted = boundary(left), boundary(right)
            if adjusted[0] < adjusted[1] or left == right:
                transformed[sid] = adjusted
    return transformed


FIND_SPANS_QUERY: LiteralString = """
MATCH (edition:Edition {id: $edition_id})
CALL (edition) {
    MATCH (edition)-[:HAS_SEGMENTATION]->(collection:Segmentation)<-[:SEGMENT_OF]-(entity:Segment)
    RETURN collection, entity
    UNION ALL
    MATCH (edition)<-[:PAGINATION_OF]-(collection:Pagination)<-[:VOLUME_OF]-(:Volume)<-[:PAGE_OF]-(entity:Page)
    RETURN collection, entity
    UNION ALL
    MATCH (edition)<-[:NOTE_OF|MARK_OF|BIBLIOGRAPHY_OF|ATTRIBUTE_OF]-(entity)
    RETURN null AS collection, entity
    UNION ALL
    MATCH (edition)<-[:TOC_OF]-(:TableOfContents)<-[:SECTION_OF]-(entity:TableOfContentsSection)
    RETURN null AS collection, entity
    UNION ALL
    MATCH (edition)<-[:FOLLOWS]-(:Recording)<-[:CUE_OF]-(entity:Cue)
    RETURN null AS collection, entity
}
MATCH (span:Span)-[:SPAN_OF]->(entity)
RETURN elementId(span) AS span_id, span.start AS start, span.end AS end, entity:Note AS is_note,
       collection {.id, entity_id: entity.id, is_page: entity:Page} AS line_group
ORDER BY start, end, span_id
"""

BATCH_UPDATE_SPANS_QUERY: LiteralString = """
UNWIND $updates AS u
MATCH (span:Span)
WHERE elementId(span) = u.span_id
SET span.start = u.new_start, span.end = u.new_end
FINISH
"""

BATCH_DELETE_SPANS_QUERY: LiteralString = """
UNWIND $span_ids AS span_id
MATCH (span:Span)-[:SPAN_OF]->(
    entity:Segment|Page|BibliographicMetadata|Note|Mark|Attribute|TableOfContentsSection|Cue
)
WHERE elementId(span) = span_id
DETACH DELETE span
WITH DISTINCT entity
WHERE NOT (:Span)-[:SPAN_OF]->(entity)
CALL (entity) {
    MATCH (child:TableOfContentsSection)-[:SUBSECTION_OF*1..]->(entity)
    OPTIONAL MATCH (child_span:Span)-[:SPAN_OF]->(child)
    OPTIONAL MATCH (child)-[:HAS_TITLE|HAS_SUMMARY]->(n:Nomen)-[:HAS_LOCALIZATION]->(lt:LocalizedText)
    DETACH DELETE child_span, lt, n, child
}
OPTIONAL MATCH (entity)-[:HAS_TITLE|HAS_SUMMARY]->(nomen:Nomen)
OPTIONAL MATCH (nomen)-[:HAS_LOCALIZATION]->(localized:LocalizedText)
OPTIONAL MATCH (entity)-[:HAS_METADATA]->(metadata:AnnotationMetadata)
DETACH DELETE localized, nomen, metadata, entity
FINISH
"""


async def adjust_spans(tx: AsyncManagedTransaction, edition_id: str, start: int, end: int, new_len: int) -> None:
    """Adjust spans after the caller locks the edition and validates the content edit."""
    updates: list[dict[str, str | int]] = []
    deletes: list[str] = []

    def record_change(span_id: str, old: tuple[int, int], new: tuple[int, int] | None) -> None:
        if new is None:
            deletes.append(span_id)
        elif new != old:
            span = Span(start=new[0], end=new[1])
            updates.append({"span_id": span_id, "new_start": span.start, "new_end": span.end})

    entities: dict[tuple[str, str, bool], list[tuple[str, int, int]]] = {}
    result = await tx.run(FIND_SPANS_QUERY, edition_id=edition_id)
    async for row in result:
        sid, left, right = row["span_id"], row["start"], row["end"]
        if (group := row["line_group"]) is None:
            new = adjust_annotation_span(left, right, start, end, new_len, clip_replacement_edges=row["is_note"])
            record_change(sid, (left, right), new)
        else:
            key = (group["id"], group["entity_id"], group["is_page"])
            entities.setdefault(key, []).append((sid, left, right))

    adjusted_spans = _adjust_continuous_entities(entities, start, end, new_len)
    for lines in entities.values():
        for sid, left, right in lines:
            record_change(sid, (left, right), adjusted_spans.get(sid))

    if updates:
        await tx.run(BATCH_UPDATE_SPANS_QUERY, updates=updates)
    if deletes:
        await tx.run(BATCH_DELETE_SPANS_QUERY, span_ids=deletes)
