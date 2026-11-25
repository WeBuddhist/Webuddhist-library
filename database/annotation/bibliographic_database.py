from typing import TYPE_CHECKING, LiteralString

from database.content_state import touch_annotation
from database.excerpt_database import read_annotations
from database.repository import SessionRepository

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction

from models.annotation import BibliographicMetadataInput, BibliographicMetadataOutput

from .single_span import (
    CREATE_SPAN_AND_METADATA,
    DELETE_SPAN_AND_METADATA,
    SINGLE_SPAN_RETURN,
    create_single_span_annotation,
)


class BibliographicDatabase(SessionRepository):
    GET_BY_ID_QUERY: LiteralString = f"""
    MATCH (span:Span)-[:SPAN_OF]->(annotation:BibliographicMetadata {{id: $bibliographic_id}})
        -[:BIBLIOGRAPHY_OF]->(edition:Edition)-[:EDITION_OF]->(text:Text)

    MATCH (annotation)-[:HAS_TYPE]->(annotation_type:BibliographyType)
    RETURN annotation_type.name AS type, {SINGLE_SPAN_RETURN}
    ORDER BY span.start, span.end
    """

    GET_BY_EDITION_ID_QUERY: LiteralString = f"""
    MATCH (edition:Edition {{id: $edition_id}})<-[:BIBLIOGRAPHY_OF]-(annotation:BibliographicMetadata)
        -[:HAS_TYPE]->(annotation_type:BibliographyType),
        (edition)-[:EDITION_OF]->(text:Text)
    MATCH (span:Span)-[:SPAN_OF]->(annotation)

    RETURN annotation_type.name AS type, {SINGLE_SPAN_RETURN}
    ORDER BY span.start, span.end
    """

    CREATE_QUERY: LiteralString = f"""
    MATCH (edition:Edition {{id: $edition_id}})
    MATCH (annotation_type:BibliographyType {{name: $type}})
    CREATE (annotation:BibliographicMetadata {{id: $annotation_id}})-[:BIBLIOGRAPHY_OF]->(edition),
        (annotation)-[:HAS_TYPE]->(annotation_type)
    {CREATE_SPAN_AND_METADATA}
    """

    DELETE_QUERY: LiteralString = f"""
    MATCH (annotation:BibliographicMetadata {{id: $bibliographic_id}})
    {DELETE_SPAN_AND_METADATA}
    """

    DELETE_ALL_QUERY: LiteralString = f"""
    MATCH (annotation:BibliographicMetadata)-[:BIBLIOGRAPHY_OF]->(:Edition {{id: $edition_id}})
    {DELETE_SPAN_AND_METADATA}
    """

    async def get(self, bibliographic_id: str) -> BibliographicMetadataOutput:
        return await self.query_one(
            BibliographicDatabase.GET_BY_ID_QUERY,
            BibliographicMetadataOutput,
            f"Bibliographic metadata with ID '{bibliographic_id}' not found",
            bibliographic_id=bibliographic_id,
        )

    async def get_all(self, edition_id: str) -> list[BibliographicMetadataOutput]:
        query = BibliographicDatabase.GET_BY_EDITION_ID_QUERY
        return await self.execute_read(read_annotations, edition_id, query, BibliographicMetadataOutput)

    async def add(
        self,
        edition_id: str,
        item: BibliographicMetadataInput,
    ) -> str:
        return await self.execute_write(BibliographicDatabase.add_with_transaction, edition_id, item)

    @staticmethod
    async def add_with_transaction(
        tx: AsyncManagedTransaction,
        edition_id: str,
        item: BibliographicMetadataInput,
    ) -> str:
        return await create_single_span_annotation(
            tx, BibliographicDatabase.CREATE_QUERY, edition_id, item, type=item.type.value
        )

    @staticmethod
    async def delete_with_transaction(tx: AsyncManagedTransaction, bibliographic_id: str) -> None:
        await touch_annotation(tx, "BibliographicMetadata", bibliographic_id)
        await tx.run(BibliographicDatabase.DELETE_QUERY, bibliographic_id=bibliographic_id)

    async def delete(self, bibliographic_id: str) -> None:
        await self.execute_write(BibliographicDatabase.delete_with_transaction, bibliographic_id)
