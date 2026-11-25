from typing import TYPE_CHECKING, Any, LiteralString

from neo4j.exceptions import ResultNotSingleError

from database.content_state import advance_revision, lock_connected_editions, read_value, touch_edition
from database.cypher import segment_lines
from database.excerpt_database import read_edition
from database.repository import SessionRepository, fetch_all, fetch_one, require
from exceptions import DataConflictError, DataNotFoundError

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction

    from database.excerpt_database import ExcerptMap
from database.database_validator import DatabaseValidator
from identifier import generate_id
from models.annotation import SegmentationInput, SegmentationOutput, SegmentOutput


class SegmentationDatabase(SessionRepository):
    _GET_PARENT_QUERY_BODY: LiteralString = """
    MATCH (edition)-[:EDITION_OF]->(text:Text)
    RETURN segmentation.id AS id,
           edition.id AS edition_id,
           text.id AS text_id,
           [(segmentation)-[:HAS_METADATA]->(m:AnnotationMetadata) | m {.name}][0] AS metadata
    ORDER BY id
    """

    _SEGMENT_LINES: LiteralString = f"""
    MATCH (segment:Segment)-[:SEGMENT_OF]->(segmentation)
    {segment_lines("segment")}
    WITH segment, lines, min_start
    WHERE size(lines) > 0
    """

    _SEGMENTS_RETURN: LiteralString = """
    ORDER BY min_start, lines[-1].end, segment.id
    RETURN segment.id AS id, segment.type AS type, segment.reference AS reference, lines
    """

    _GET_SEGMENTS_QUERY_BODY: LiteralString = _SEGMENT_LINES + _SEGMENTS_RETURN

    GET_SEGMENTS_IN_RANGE_QUERY: LiteralString = f"""
    MATCH (:Edition {{id: $edition_id}})-[:HAS_SEGMENTATION]->(segmentation:Segmentation)
    {_SEGMENT_LINES}
      AND min_start <= $end AND lines[-1].end >= $start
    {_SEGMENTS_RETURN}
    """

    GET_BY_EDITION_ID_QUERY: LiteralString = f"""
    MATCH (edition:Edition {{id: $edition_id}})-[:HAS_SEGMENTATION]->(segmentation:Segmentation)
    {_GET_PARENT_QUERY_BODY}
    """

    GET_SEGMENTS_BY_EDITION_ID_QUERY: LiteralString = f"""
    MATCH (:Edition {{id: $edition_id}})-[:HAS_SEGMENTATION]->(segmentation:Segmentation)
    {_GET_SEGMENTS_QUERY_BODY}
    SKIP $offset
    LIMIT $limit
    """

    GET_ALL_SEGMENTS_BY_EDITION_ID_QUERY: LiteralString = f"""
    MATCH (:Edition {{id: $edition_id}})-[:HAS_SEGMENTATION]->(segmentation:Segmentation)
    {_GET_SEGMENTS_QUERY_BODY}
    """

    CREATE_QUERY: LiteralString = """
    MATCH (edition:Edition {id: $edition_id})
    WITH edition
    WHERE NOT EXISTS { (edition)-[:HAS_SEGMENTATION]->(:Segmentation) }
    CREATE (edition)-[:HAS_SEGMENTATION]->(segmentation:Segmentation {id: $segmentation_id})
    WITH segmentation
    FOREACH (metadata IN $metadata |
        CREATE (segmentation)-[:HAS_METADATA]->(:AnnotationMetadata {id: metadata.id, name: metadata.name})
    )
    WITH segmentation
    UNWIND $segments AS segment_data
    CREATE (segment:Segment {id: segment_data.id})-[:SEGMENT_OF]->(segmentation)
    SET segment.reference = segment_data.reference,
        segment.type = segment_data.type
    WITH segmentation, segment, segment_data
    UNWIND segment_data.lines AS line
    CREATE (:Span {start: line.start, end: line.end})-[:SPAN_OF]->(segment)
    RETURN segmentation.id AS id, count(*) AS segment_count
    """

    DELETE_BY_EDITION_ID_QUERY: LiteralString = """
    MATCH (edition:Edition {id: $edition_id})
    OPTIONAL MATCH (edition)-[:HAS_SEGMENTATION]->(segmentation:Segmentation)
    OPTIONAL MATCH (segment:Segment)-[:SEGMENT_OF]->(segmentation)
    OPTIONAL MATCH (span:Span)-[:SPAN_OF]->(segment)
    OPTIONAL MATCH (segmentation)-[:HAS_METADATA]->(metadata:AnnotationMetadata)
    DETACH DELETE span, segment, metadata, segmentation
    FINISH
    """

    async def get_by_edition(self, edition_id: str) -> SegmentationOutput:
        """An excerpt edition's segmentation is its source's, identified by the excerpt edition."""

        async def read(tx: AsyncManagedTransaction, source_id: str, excerpt: ExcerptMap | None) -> SegmentationOutput:
            segmentation = await SegmentationDatabase.get_by_edition_with_transaction(tx, source_id)
            if excerpt is None:
                return segmentation
            return segmentation.model_copy(update=excerpt.owner | {"id": excerpt.edition_id})

        return await self.execute_read(read_edition, edition_id, read)

    async def get_segments_by_edition(
        self,
        edition_id: str,
        *,
        offset: int,
        limit: int,
    ) -> list[SegmentOutput]:
        """An excerpt edition gets the source segments overlapping each of its spans, trimmed to it."""

        async def read(tx: AsyncManagedTransaction, source_id: str, excerpt: ExcerptMap | None) -> list[SegmentOutput]:
            if excerpt is None:
                return await SegmentationDatabase.get_segments_by_edition_with_transaction(
                    tx, source_id, offset=offset, limit=limit
                )
            await SegmentationDatabase.get_by_edition_with_transaction(tx, source_id)
            segments: list[SegmentOutput] = []
            for piece in excerpt.slices:
                query = SegmentationDatabase.GET_SEGMENTS_IN_RANGE_QUERY
                found = await fetch_all(
                    tx, query, SegmentOutput, edition_id=source_id, start=piece.start, end=piece.end
                )
                segments += piece.project_segments(found)
            return segments[offset : offset + limit]

        return await self.execute_read(read_edition, edition_id, read)

    async def get_all_segments_by_edition(self, edition_id: str) -> list[SegmentOutput]:
        query = SegmentationDatabase.GET_ALL_SEGMENTS_BY_EDITION_ID_QUERY
        return await self.execute_read(
            read_value, edition_id, lambda tx: SegmentationDatabase._segments(tx, query, edition_id)
        )

    @staticmethod
    async def add_with_transaction(
        tx: AsyncManagedTransaction, edition_id: str, segmentation: SegmentationInput
    ) -> str:
        state = await touch_edition(tx, edition_id)
        state.validate_span(segmentation.max_end)

        segmentation_id = generate_id()

        segments_data = [seg.model_dump(mode="json") | {"id": generate_id()} for seg in segmentation.segments]

        result = await tx.run(
            SegmentationDatabase.CREATE_QUERY,
            edition_id=edition_id,
            segmentation_id=segmentation_id,
            segments=segments_data,
            metadata=[{"id": generate_id(), "name": segmentation.metadata.name}]
            if segmentation.metadata is not None
            else [],
        )
        try:
            record = await result.single(strict=True)
        except ResultNotSingleError as exc:
            raise DataConflictError(f"Edition with ID '{edition_id}' already has a segmentation") from exc
        if record["segment_count"] == 0:
            raise DataConflictError(f"Edition with ID '{edition_id}' already has a segmentation")
        return str(record["id"])

    async def add(self, edition_id: str, segmentation: SegmentationInput) -> str:
        return await self.execute_write(SegmentationDatabase.add_with_transaction, edition_id, segmentation)

    @staticmethod
    async def get_by_edition_with_transaction(tx: AsyncManagedTransaction, edition_id: str) -> SegmentationOutput:
        await DatabaseValidator.validate_edition_exists(tx, edition_id)
        return await fetch_one(
            tx,
            SegmentationDatabase.GET_BY_EDITION_ID_QUERY,
            SegmentationOutput,
            f"Segmentation for edition '{edition_id}' not found",
            edition_id=edition_id,
        )

    @staticmethod
    async def get_segments_by_edition_with_transaction(
        tx: AsyncManagedTransaction,
        edition_id: str,
        *,
        offset: int,
        limit: int,
    ) -> list[SegmentOutput]:
        query = SegmentationDatabase.GET_SEGMENTS_BY_EDITION_ID_QUERY
        return await SegmentationDatabase._segments(tx, query, edition_id, offset=offset, limit=limit)

    @staticmethod
    async def _segments(
        tx: AsyncManagedTransaction,
        query: LiteralString,
        edition_id: str,
        **params: Any,  # noqa: ANN401
    ) -> list[SegmentOutput]:
        await DatabaseValidator.validate_edition_exists(tx, edition_id)
        segments = await fetch_all(tx, query, SegmentOutput, edition_id=edition_id, **params)
        if not segments:
            await SegmentationDatabase._validate_edition_has_segmentation(tx, edition_id)
        return segments

    @staticmethod
    async def _validate_edition_has_segmentation(tx: AsyncManagedTransaction, edition_id: str) -> None:
        await require(
            tx,
            "(:Edition {id: $edition_id})-[:HAS_SEGMENTATION]->(:Segmentation)",
            DataNotFoundError(f"Segmentation for edition '{edition_id}' not found"),
            edition_id=edition_id,
        )

    async def delete_by_edition(self, edition_id: str) -> None:
        await self.execute_write(SegmentationDatabase.delete_by_edition_with_transaction, edition_id)

    @staticmethod
    async def delete_by_edition_with_transaction(tx: AsyncManagedTransaction, edition_id: str) -> None:
        related = await lock_connected_editions(tx, edition_id)
        await touch_edition(tx, edition_id)
        for related_id in related:
            await advance_revision(tx, related_id)
        await tx.run(SegmentationDatabase.DELETE_BY_EDITION_ID_QUERY, edition_id=edition_id)
