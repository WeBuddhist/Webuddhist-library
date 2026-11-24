from typing import TYPE_CHECKING, Literal, LiteralString

from exceptions import DataValidationError

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
    start: int, end: int, edit_start: int, edit_end: int, new_len: int
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
    return (
        _map_boundary(start, edit_start, edit_end, new_len, side="right"),
        _map_boundary(end, edit_start, edit_end, new_len, side="left" if end == edit_start else "right"),
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
    ordered = sorted(entities.items(), key=lambda item: (item[0][0], item[1][0][1], item[1][-1][2], item[0][1]))
    # Split at the first fully covered entity so it owns the replacement. Otherwise split at the edit start.
    pivots: dict[str, int] = {}
    for (collection_id, _, _), lines in ordered:
        left, right = lines[0][1], lines[-1][2]
        if start <= left < right <= end:
            pivots.setdefault(collection_id, left)

    transformed: dict[str, tuple[int, int]] = {}
    for (collection_id, _, is_page), lines in ordered:

        def boundary(position: int, pivot: int = pivots.get(collection_id, start)) -> int:
            return _map_boundary(position, start, end, new_len, side="left" if position <= pivot else "right")

        if is_page and boundary(lines[0][1]) == boundary(lines[-1][2]):
            raise DataValidationError("Content edits must leave at least one character on every page")
        for sid, left, right in lines:
            adjusted = boundary(left), boundary(right)
            if adjusted[0] < adjusted[1] or left == right:
                transformed[sid] = adjusted
    return transformed


class SpanDatabase:
    FIND_CONTINUOUS_SPANS_QUERY: LiteralString = """
    CALL {
        MATCH (m:Edition {id: $edition_id})
            -[:HAS_SEGMENTATION]->(collection:Segmentation)
            <-[:SEGMENT_OF]-(entity:Segment)
            <-[:SPAN_OF]-(span:Span)
        RETURN collection.id AS collection_id, entity.id AS entity_id, entity:Page AS is_page,
               elementId(span) AS span_id, span.start AS span_start, span.end AS span_end
        UNION ALL
        MATCH (m:Edition {id: $edition_id})
            <-[:PAGINATION_OF]-(collection:Pagination)
            <-[:VOLUME_OF]-(:Volume)
            <-[:PAGE_OF]-(entity:Page)
            <-[:SPAN_OF]-(span:Span)
        RETURN collection.id AS collection_id, entity.id AS entity_id, entity:Page AS is_page,
               elementId(span) AS span_id, span.start AS span_start, span.end AS span_end
    }
    RETURN collection_id, entity_id, is_page, span_id, span_start, span_end
    ORDER BY collection_id, span_start, entity_id, span_end, span_id
    """

    FIND_ANNOTATION_SPANS_QUERY: LiteralString = """
    CALL {
        MATCH (m:Edition {id: $edition_id})
            <-[:NOTE_OF|MARK_OF|BIBLIOGRAPHY_OF|ATTRIBUTE_OF]-(entity)
            <-[:SPAN_OF]-(span:Span)
        RETURN elementId(span) AS span_id, span.start AS span_start, span.end AS span_end
        UNION ALL
        MATCH (m:Edition {id: $edition_id})
            <-[:TOC_OF]-(:TableOfContents)
            <-[:SECTION_OF]-(entity:TableOfContentsSection)
            <-[:SPAN_OF]-(span:Span)
        RETURN elementId(span) AS span_id, span.start AS span_start, span.end AS span_end
        UNION ALL
        MATCH (m:Edition {id: $edition_id})
            <-[:FOLLOWS]-(:Recording)
            <-[:CUE_OF]-(entity:Cue)
            <-[:SPAN_OF]-(span:Span)
        RETURN elementId(span) AS span_id, span.start AS span_start, span.end AS span_end
    }
    RETURN span_id, span_start, span_end
    ORDER BY span_start
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

    @staticmethod
    async def _flush_batch(
        tx: AsyncManagedTransaction,
        updates: list[dict[str, str | int]],
        deletes: list[str],
    ) -> None:
        """Execute batched span updates and deletions."""
        if updates:
            await tx.run(SpanDatabase.BATCH_UPDATE_SPANS_QUERY, updates=updates)
        if deletes:
            await tx.run(SpanDatabase.BATCH_DELETE_SPANS_QUERY, span_ids=deletes)

    @staticmethod
    async def adjust_with_transaction(
        tx: AsyncManagedTransaction, edition_id: str, start: int, end: int, new_len: int
    ) -> None:
        """Adjust spans after the caller locks the edition and validates the content edit."""
        updates: list[dict[str, str | int]] = []
        deletes: list[str] = []

        def record_change(span_id: str, old: tuple[int, int], new: tuple[int, int] | None) -> None:
            if new is None:
                deletes.append(span_id)
            elif new != old:
                updates.append({"span_id": span_id, "new_start": new[0], "new_end": new[1]})

        result = await tx.run(SpanDatabase.FIND_CONTINUOUS_SPANS_QUERY, edition_id=edition_id)
        entities: dict[tuple[str, str, bool], list[tuple[str, int, int]]] = {}
        for record in await result.data():
            key = (record["collection_id"], record["entity_id"], record["is_page"])
            entities.setdefault(key, []).append((record["span_id"], record["span_start"], record["span_end"]))
        adjusted_spans = _adjust_continuous_entities(entities, start, end, new_len)
        for lines in entities.values():
            for sid, left, right in lines:
                record_change(sid, (left, right), adjusted_spans.get(sid))

        result = await tx.run(SpanDatabase.FIND_ANNOTATION_SPANS_QUERY, edition_id=edition_id)
        for record in await result.data():
            old = (record["span_start"], record["span_end"])
            record_change(record["span_id"], old, adjust_annotation_span(*old, start, end, new_len))

        await SpanDatabase._flush_batch(tx, updates, deletes)
