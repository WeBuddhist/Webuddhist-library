from typing import TYPE_CHECKING, LiteralString

from database.content_state import touch_annotation
from database.excerpt_database import read_edition
from database.repository import SessionRepository
from exceptions import DataNotFoundError

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction

    from database.excerpt_database import ExcerptMap
from models.annotation import MarkInput, MarkOutput
from models.enums import MarkType

from .single_span import (
    CREATE_SPAN_AND_METADATA,
    DELETE_SPAN_AND_METADATA,
    SINGLE_SPAN_RETURN,
    create_single_span_annotation,
)


class MarkDatabase(SessionRepository):
    GET_BY_ID_QUERY: LiteralString = f"""
    MATCH (span:Span)-[:SPAN_OF]->(annotation:Mark {{id: $mark_id}})
        -[:MARK_OF]->(edition:Edition)-[:EDITION_OF]->(text:Text)

    RETURN {SINGLE_SPAN_RETURN}
    ORDER BY span.start, span.end
    """

    GET_BY_EDITION_ID_QUERY: LiteralString = f"""
    MATCH (edition:Edition {{id: $edition_id}})<-[:MARK_OF]-(annotation:Mark)
        -[:HAS_TYPE]->(annotation_type:MarkType {{name: $mark_type}}),
        (edition)-[:EDITION_OF]->(text:Text)
    MATCH (span:Span)-[:SPAN_OF]->(annotation)

    RETURN {SINGLE_SPAN_RETURN}
    ORDER BY span.start, span.end
    """

    CREATE_QUERY: LiteralString = f"""
    MATCH (edition:Edition {{id: $edition_id}})
    MERGE (annotation_type:MarkType {{name: $mark_type}})
    CREATE (annotation:Mark {{id: $annotation_id}})-[:MARK_OF]->(edition),
        (annotation)-[:HAS_TYPE]->(annotation_type)
    {CREATE_SPAN_AND_METADATA}
    """

    DELETE_QUERY: LiteralString = f"""
    MATCH (annotation:Mark {{id: $mark_id}})
    {DELETE_SPAN_AND_METADATA}
    """

    DELETE_ALL_QUERY: LiteralString = f"""
    MATCH (annotation:Mark)-[:MARK_OF]->(:Edition {{id: $edition_id}})
    {DELETE_SPAN_AND_METADATA}
    """

    async def get(self, mark_id: str) -> MarkOutput:
        async def read(tx: AsyncManagedTransaction) -> MarkOutput:
            result = await tx.run(MarkDatabase.GET_BY_ID_QUERY, mark_id=mark_id)
            record = await result.single()
            if record is None:
                raise DataNotFoundError(f"Mark with ID '{mark_id}' not found")
            return MarkOutput.model_validate(record)

        return await self.execute_read(read)

    async def get_all(
        self,
        edition_id: str,
        mark_type: MarkType = MarkType.YIGCHUNG,
    ) -> list[MarkOutput]:
        async def read(tx: AsyncManagedTransaction, source_id: str, excerpt: ExcerptMap | None) -> list[MarkOutput]:
            result = await tx.run(
                MarkDatabase.GET_BY_EDITION_ID_QUERY,
                edition_id=source_id,
                mark_type=mark_type.value,
            )
            marks = [MarkOutput.model_validate(record) for record in await result.data()]
            return excerpt.project_annotations(marks) if excerpt else marks

        return await self.execute_read(read_edition, edition_id, read)

    async def add_yigchung(self, edition_id: str, mark: MarkInput) -> str:
        return (await self.add_yigchungs(edition_id, [mark]))[0]

    async def add_yigchungs(self, edition_id: str, marks: list[MarkInput]) -> list[str]:
        async def add_all(tx: AsyncManagedTransaction) -> list[str]:
            return [await MarkDatabase.add_with_transaction(tx, edition_id, mark, MarkType.YIGCHUNG) for mark in marks]

        return await self.execute_write(add_all)

    @staticmethod
    async def add_with_transaction(
        tx: AsyncManagedTransaction,
        edition_id: str,
        mark: MarkInput,
        mark_type: MarkType,
    ) -> str:
        return await create_single_span_annotation(
            tx, MarkDatabase.CREATE_QUERY, edition_id, mark, mark_type=mark_type.value
        )

    @staticmethod
    async def delete_with_transaction(tx: AsyncManagedTransaction, mark_id: str) -> None:
        await touch_annotation(tx, "Mark", mark_id)
        await tx.run(MarkDatabase.DELETE_QUERY, mark_id=mark_id)

    async def delete(self, mark_id: str) -> None:
        await self.execute_write(MarkDatabase.delete_with_transaction, mark_id)
