from typing import TYPE_CHECKING, LiteralString

from database.content_state import touch_annotation
from database.excerpt import read_annotations
from database.repository import SessionSource, query_one, writing
from models.annotation import MarkInput, MarkOutput
from models.enums import MarkType

from .single_span import (
    CREATE_SPAN_AND_METADATA,
    DELETE_SPAN_AND_METADATA,
    SINGLE_SPAN_RETURN,
    create_single_span_annotation,
)

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction


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


@writing
async def add(tx: AsyncManagedTransaction, edition_id: str, mark: MarkInput, mark_type: MarkType) -> str:
    return await create_single_span_annotation(tx, CREATE_QUERY, edition_id, mark, mark_type=mark_type.value)


@writing
async def delete(tx: AsyncManagedTransaction, mark_id: str) -> None:
    await touch_annotation(tx, "Mark", mark_id)
    await tx.run(DELETE_QUERY, mark_id=mark_id)


async def get(db: SessionSource, mark_id: str) -> MarkOutput:
    return await query_one(db, GET_BY_ID_QUERY, MarkOutput, f"Mark with ID '{mark_id}' not found", mark_id=mark_id)


async def get_all(db: SessionSource, edition_id: str, mark_type: MarkType = MarkType.YIGCHUNG) -> list[MarkOutput]:
    return await db.read(read_annotations, edition_id, GET_BY_EDITION_ID_QUERY, MarkOutput, mark_type=mark_type.value)


async def add_yigchung(db: SessionSource, edition_id: str, mark: MarkInput) -> str:
    return await add(db, edition_id, mark, MarkType.YIGCHUNG)


@writing
async def add_yigchungs(tx: AsyncManagedTransaction, edition_id: str, marks: list[MarkInput]) -> list[str]:
    return [await add(tx, edition_id, mark, MarkType.YIGCHUNG) for mark in marks]
