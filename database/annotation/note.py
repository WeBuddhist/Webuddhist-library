from typing import TYPE_CHECKING, LiteralString

from database.content_state import touch_edition
from database.excerpt import read_annotations
from database.repository import SessionSource, query_one, writing
from models.annotation import FootnoteInput, NoteInput, NoteOutput, Span
from models.enums import NoteType

from .single_span import (
    CREATE_SPAN_AND_METADATA,
    DELETE_SPAN_AND_METADATA,
    SINGLE_SPAN_RETURN,
    create_single_span_annotation,
)

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction


GET_BY_ID_QUERY: LiteralString = f"""
MATCH (span:Span)-[:SPAN_OF]->(annotation:Note {{id: $note_id}})
    -[:HAS_TYPE]->(:NoteType {{name: $note_type}}),
    (annotation)-[:NOTE_OF]->(edition:Edition)-[:EDITION_OF]->(text:Text)

RETURN annotation.text AS text, {SINGLE_SPAN_RETURN}
ORDER BY span.start, span.end
"""
GET_BY_EDITION_ID_QUERY: LiteralString = f"""
MATCH (edition:Edition {{id: $edition_id}})<-[:NOTE_OF]-(annotation:Note)
    -[:HAS_TYPE]->(annotation_type:NoteType {{name: $note_type}}),
    (edition)-[:EDITION_OF]->(text:Text)
MATCH (span:Span)-[:SPAN_OF]->(annotation)

RETURN annotation.text AS text, {SINGLE_SPAN_RETURN}
ORDER BY span.start, span.end, id
"""
CREATE_QUERY: LiteralString = f"""
MATCH (edition:Edition {{id: $edition_id}})
MERGE (annotation_type:NoteType {{name: $note_type}})
CREATE (annotation:Note {{text: $text, id: $annotation_id}})-[:NOTE_OF]->(edition),
    (annotation)-[:HAS_TYPE]->(annotation_type)
{CREATE_SPAN_AND_METADATA}
"""
GET_EDITION_ID_QUERY: LiteralString = """
MATCH (:NoteType {name: $note_type})<-[:HAS_TYPE]-(:Note {id: $note_id})-[:NOTE_OF]->(edition:Edition)
RETURN edition.id AS edition_id
"""
DELETE_QUERY: LiteralString = f"""
MATCH (annotation:Note {{id: $note_id}})-[:HAS_TYPE]->(:NoteType {{name: $note_type}})
{DELETE_SPAN_AND_METADATA}
"""
DELETE_ALL_BY_TYPE_QUERY: LiteralString = f"""
MATCH (annotation:Note)-[:NOTE_OF]->(:Edition {{id: $edition_id}}),
    (annotation)-[:HAS_TYPE]->(:NoteType {{name: $note_type}})
{DELETE_SPAN_AND_METADATA}
"""
DELETE_ALL_QUERY: LiteralString = f"""
MATCH (annotation:Note)-[:NOTE_OF]->(:Edition {{id: $edition_id}})
{DELETE_SPAN_AND_METADATA}
"""


@writing
async def add(tx: AsyncManagedTransaction, edition_id: str, note: NoteInput, note_type: NoteType) -> str:
    return await create_single_span_annotation(
        tx, CREATE_QUERY, edition_id, note, text=note.text, note_type=str(note_type)
    )


@writing
async def add_footnote(tx: AsyncManagedTransaction, edition_id: str, note: FootnoteInput) -> str:
    point = NoteInput(span=Span(start=note.position, end=note.position), text=note.text, metadata=note.metadata)
    return await add(tx, edition_id, point, NoteType.FOOTNOTE)


@writing
async def delete(tx: AsyncManagedTransaction, note_id: str, note_type: NoteType = NoteType.DURCHEN) -> None:
    result = await tx.run(GET_EDITION_ID_QUERY, note_id=note_id, note_type=str(note_type))
    record = await result.single()
    if record is None:
        return
    await touch_edition(tx, record["edition_id"])
    await tx.run(DELETE_QUERY, note_id=note_id, note_type=str(note_type))


async def get(db: SessionSource, note_id: str, note_type: NoteType = NoteType.DURCHEN) -> NoteOutput:
    return await query_one(
        db,
        GET_BY_ID_QUERY,
        NoteOutput,
        f"Note with ID '{note_id}' not found",
        note_id=note_id,
        note_type=str(note_type),
    )


async def get_all(db: SessionSource, edition_id: str, note_type: NoteType = NoteType.DURCHEN) -> list[NoteOutput]:
    """An excerpt edition gets the source's notes that lie wholly inside one of its spans."""
    return await db.read(
        read_annotations, edition_id, GET_BY_EDITION_ID_QUERY, NoteOutput, whole=True, note_type=str(note_type)
    )


async def add_durchen(db: SessionSource, edition_id: str, note: NoteInput) -> str:
    return await add(db, edition_id, note, NoteType.DURCHEN)


@writing
async def add_durchens(tx: AsyncManagedTransaction, edition_id: str, notes: list[NoteInput]) -> list[str]:
    return [await add(tx, edition_id, note, NoteType.DURCHEN) for note in notes]


@writing
async def add_footnotes(tx: AsyncManagedTransaction, edition_id: str, notes: list[FootnoteInput]) -> list[str]:
    return [await add_footnote(tx, edition_id, note) for note in notes]


@writing
async def delete_all_footnotes(tx: AsyncManagedTransaction, edition_id: str) -> None:
    await touch_edition(tx, edition_id)
    await tx.run(DELETE_ALL_BY_TYPE_QUERY, edition_id=edition_id, note_type=str(NoteType.FOOTNOTE))
