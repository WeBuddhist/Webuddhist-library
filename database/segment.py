from typing import TYPE_CHECKING, LiteralString

from database import language
from database.content_state import read_at_revision, read_revisions, read_value
from database.cypher import application_tag_ids, has_language, segment_lines
from database.repository import fetch_all, fetch_one, fetch_record, reading
from errors import DataConflictError
from models.annotation import SegmentWithContextOutput, Span
from models.requests import RelatedSegmentsFilter

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction

GET_QUERY: LiteralString = f"""
MATCH (seg:Segment {{id: $segment_id}})-[:SEGMENT_OF]->(segmentation:Segmentation)
    <-[:HAS_SEGMENTATION]-(edition:Edition)-[:EDITION_OF]->(text:Text)
{segment_lines("seg")}
WITH * WHERE size(lines) > 0
RETURN seg.id AS id, segmentation.id AS segmentation_id,
    edition.id AS edition_id, text.id AS text_id,
    seg.type AS type,
    seg.reference AS reference,
    lines,
    nullIf({application_tag_ids("(seg)")}, []) AS tag_ids
"""
FIND_BY_SPANS_QUERY: LiteralString = """
MATCH (:Edition {id: $edition_id})
    -[:HAS_SEGMENTATION]->(:Segmentation)
    <-[:SEGMENT_OF]-(seg:Segment)
    <-[:SPAN_OF]-(span:Span)
WHERE ANY(sp IN $spans WHERE
    (span.start < sp[1] AND span.end > sp[0])
    OR (span.start = span.end AND sp[0] <= span.start AND span.start < sp[1])
    OR (sp[0] = sp[1] AND span.start <= sp[0] AND
        (sp[0] < span.end OR (span.start = span.end AND span.start = sp[0]))))
RETURN DISTINCT seg.id AS segment_id
ORDER BY segment_id
"""
QUERY_DISCOVER: LiteralString = """
UNWIND $segment_ids AS segment_id
MATCH (:Segment {id: segment_id})-[:ALIGNED_TO]-(remote_seg:Segment)
    -[:SEGMENT_OF]->(:Segmentation)<-[:HAS_SEGMENTATION]-(remote_edition:Edition)
RETURN DISTINCT remote_seg.id AS segment_id, remote_edition.id AS edition_id
ORDER BY edition_id, segment_id
"""
RESOLVE_SEGMENT_PAGE_QUERY: LiteralString = f"""
UNWIND $segment_ids AS segment_id
MATCH (seg:Segment {{id: segment_id}})-[:SEGMENT_OF]->(sgn:Segmentation)
    <-[:HAS_SEGMENTATION]-(edition:Edition)-[:EDITION_OF]->(text:Text)
WHERE ($text_id IS NULL OR text.id = $text_id)
  AND ($filter_edition_id IS NULL OR edition.id = $filter_edition_id)
  AND {has_language("text")}
  AND (size($licenses) = 0 OR EXISTS {{ (text)-[:HAS_LICENSE]->(l:LicenseType) WHERE l.name IN $licenses }})
{segment_lines("seg")}
WITH text, edition, sgn, seg, lines, min_start
WHERE size(lines) > 0
WITH text, edition, sgn, seg, lines, min_start, nullIf({application_tag_ids("(seg)")}, []) AS tag_ids
ORDER BY text.id, edition.id, sgn.id, min_start, lines[-1].end, seg.id
SKIP $offset
LIMIT $limit
RETURN seg.id AS id, sgn.id AS segmentation_id,
       edition.id AS edition_id, text.id AS text_id,
       seg.type AS type,
       seg.reference AS reference, lines, tag_ids
"""
SEGMENT_REVISIONS_QUERY: LiteralString = """
UNWIND $ids AS id
MATCH (:Segment {id: id})-[:SEGMENT_OF]->(:Segmentation)<-[:HAS_SEGMENTATION]-(e:Edition)
RETURN DISTINCT e.id AS id, e.revision AS revision
"""


@reading
async def get(tx: AsyncManagedTransaction, segment_id: str, application: str | None = None) -> SegmentWithContextOutput:
    return await fetch_one(
        tx,
        GET_QUERY,
        SegmentWithContextOutput,
        f"Segment '{segment_id}' not found",
        segment_id=segment_id,
        application=application,
    )


async def _find_by_spans(tx: AsyncManagedTransaction, edition_id: str, spans: list[tuple[int, int]]) -> list[str]:
    result = await tx.run(FIND_BY_SPANS_QUERY, edition_id=edition_id, spans=spans)
    return await result.value("segment_id")


@reading
async def get_content_location(
    tx: AsyncManagedTransaction, segment_id: str, application: str | None = None
) -> tuple[str, Span]:
    """Read the immutable object key and segment span at the same edition revision."""
    parent = await fetch_record(
        tx,
        """
        MATCH (:Segment {id: $id})-[:SEGMENT_OF]->(:Segmentation)<-[:HAS_SEGMENTATION]-(e:Edition)
        RETURN e.id AS id
        """,
        f"Segment '{segment_id}' not found",
        id=segment_id,
    )
    state, segment = await read_at_revision(tx, parent["id"], lambda read_tx: get(read_tx, segment_id, application))
    return (state.stored_key, segment.span)


async def _resolve_segment_page(
    tx: AsyncManagedTransaction,
    *,
    segment_ids: list[str],
    application: str | None,
    offset: int,
    limit: int,
    filters: RelatedSegmentsFilter,
    language_code: str | None,
) -> list[SegmentWithContextOutput]:
    return await fetch_all(
        tx,
        RESOLVE_SEGMENT_PAGE_QUERY,
        SegmentWithContextOutput,
        segment_ids=segment_ids,
        application=application,
        offset=offset,
        limit=limit,
        text_id=filters.text_id,
        filter_edition_id=filters.edition_id,
        language=filters.language,
        language_base=language_code,
        licenses=[license_type.value for license_type in filters.license],
    )


MAX_DEPTH = 5
_SEGMENT_EDITION_EXTENT_QUERY: LiteralString = """
MATCH (seg:Segment {id: $segment_id})-[:SEGMENT_OF]->(:Segmentation)<-[:HAS_SEGMENTATION]-(e:Edition)
OPTIONAL MATCH (seg)<-[:SPAN_OF]-(s:Span)
RETURN e.id AS edition_id, min(s.start) AS start, max(s.end) AS end
"""


async def get_related_by_spans(
    tx: AsyncManagedTransaction,
    edition_id: str,
    spans: list[tuple[int, int]],
    *,
    application: str | None = None,
    offset: int = 0,
    limit: int = 20,
    filters: RelatedSegmentsFilter | None = None,
) -> list[SegmentWithContextOutput]:
    """Traverse direct segment alignments from an edition+spans and return paged segments."""
    filters = filters or RelatedSegmentsFilter()
    revisions: dict[str, int] = {}

    async def capture_revisions(segment_ids: list[str]) -> None:
        result = await tx.run(SEGMENT_REVISIONS_QUERY, ids=segment_ids)
        async for record in result:
            if revisions.setdefault(record["id"], record["revision"]) != record["revision"]:
                raise DataConflictError("Alignment changed while reading; retry the request")

    language_code = await language.validated_language_code(tx, filters.language)
    frontier = await _find_by_spans(tx, edition_id, spans)
    if not frontier:
        return []
    related_segment_ids: set[str] = set()
    visited_editions = {edition_id}
    for _ in range(MAX_DEPTH):
        await capture_revisions(frontier)
        records = await (await tx.run(QUERY_DISCOVER, segment_ids=frontier)).data()
        new_segment_editions = {
            record["segment_id"]: record["edition_id"]
            for record in records
            if record["edition_id"] not in visited_editions
        }
        if not new_segment_editions:
            break
        visited_editions.update(new_segment_editions.values())
        frontier = sorted(new_segment_editions)
        related_segment_ids.update(frontier)
    if not related_segment_ids:
        return []
    segment_ids = sorted(related_segment_ids)
    await capture_revisions(segment_ids)
    result = await _resolve_segment_page(
        tx,
        segment_ids=segment_ids,
        application=application,
        offset=offset,
        limit=limit,
        filters=filters,
        language_code=language_code,
    )
    if await read_revisions(tx, revisions.keys()) != revisions:
        raise DataConflictError("Alignment changed while reading; retry the request")
    return result


@reading
async def get_related(
    tx: AsyncManagedTransaction,
    segment_id: str,
    *,
    application: str | None = None,
    offset: int = 0,
    limit: int = 20,
    filters: RelatedSegmentsFilter | None = None,
) -> list[SegmentWithContextOutput]:
    """Related segments for one segment; empty when the segment is missing."""
    record = await (await tx.run(_SEGMENT_EDITION_EXTENT_QUERY, segment_id=segment_id)).single()
    if record is None:
        return []
    edition_id = record["edition_id"]
    spans = [(record["start"], record["end"])] if record["start"] is not None else []

    async def read(read_tx: AsyncManagedTransaction) -> list[SegmentWithContextOutput]:
        return await get_related_by_spans(
            read_tx, edition_id, spans, application=application, offset=offset, limit=limit, filters=filters
        )

    return await read_value(tx, edition_id, read)
