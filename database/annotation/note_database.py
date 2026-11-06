from typing import TYPE_CHECKING, LiteralString

from database.content_state import read_value, touch_edition
from exceptions import DataNotFoundError

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction

    from database.database import Database
from models.annotation import FootnoteInput, NoteInput, NoteOutput, SingleSpanAnnotation, Span
from models.enums import NoteType

from .single_span import (
    CREATE_SPAN_AND_METADATA,
    DELETE_SPAN_AND_METADATA,
    SINGLE_SPAN_RETURN,
    create_single_span_annotation,
)


class NoteDatabase:
    """Notes of every type share one graph shape; durchen covers a span, footnote marks a point (empty span)."""

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

    # MERGE creates a note type on first use, so a new type needs no data migration.
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

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, note_id: str, note_type: NoteType = NoteType.DURCHEN) -> NoteOutput:
        async def read(tx: AsyncManagedTransaction) -> NoteOutput:
            result = await tx.run(NoteDatabase.GET_BY_ID_QUERY, note_id=note_id, note_type=str(note_type))
            record = await result.single()
            if record is None:
                raise DataNotFoundError(f"Note with ID '{note_id}' not found")
            return NoteOutput.model_validate(record)

        async with self._db.get_session() as session:
            return await session.execute_read(read)

    async def get_all(self, edition_id: str, note_type: NoteType = NoteType.DURCHEN) -> list[NoteOutput]:
        async def read(tx: AsyncManagedTransaction) -> list[NoteOutput]:
            result = await tx.run(
                NoteDatabase.GET_BY_EDITION_ID_QUERY,
                edition_id=edition_id,
                note_type=str(note_type),
            )
            return [NoteOutput.model_validate(record) for record in await result.data()]

        async with self._db.get_session() as session:
            return await session.execute_read(read_value, edition_id, read)

    async def add_durchen(self, edition_id: str, note: NoteInput) -> str:
        return (await self.add_durchens(edition_id, [note]))[0]

    async def add_durchens(self, edition_id: str, notes: list[NoteInput]) -> list[str]:
        async def add_all(tx: AsyncManagedTransaction) -> list[str]:
            return [await NoteDatabase.add_with_transaction(tx, edition_id, note, NoteType.DURCHEN) for note in notes]

        async with self._db.get_session() as session:
            return await session.execute_write(add_all)

    async def add_footnotes(self, edition_id: str, notes: list[FootnoteInput]) -> list[str]:
        async def add_all(tx: AsyncManagedTransaction) -> list[str]:
            return [await NoteDatabase.add_footnote_with_transaction(tx, edition_id, note) for note in notes]

        async with self._db.get_session() as session:
            return await session.execute_write(add_all)

    @staticmethod
    async def add_with_transaction(
        tx: AsyncManagedTransaction,
        edition_id: str,
        note: NoteInput,
        note_type: NoteType,
    ) -> str:
        return await create_single_span_annotation(
            tx, NoteDatabase.CREATE_QUERY, edition_id, note, text=note.text, note_type=str(note_type)
        )

    @staticmethod
    async def add_footnote_with_transaction(tx: AsyncManagedTransaction, edition_id: str, note: FootnoteInput) -> str:
        point = SingleSpanAnnotation(span=Span(start=note.position, end=note.position), metadata=note.metadata)
        return await create_single_span_annotation(
            tx, NoteDatabase.CREATE_QUERY, edition_id, point, text=note.text, note_type=str(NoteType.FOOTNOTE)
        )

    @staticmethod
    async def delete_with_transaction(
        tx: AsyncManagedTransaction, note_id: str, note_type: NoteType = NoteType.DURCHEN
    ) -> None:
        # Look the note up by type first: an ID of another note type deletes nothing and must not touch its edition.
        result = await tx.run(NoteDatabase.GET_EDITION_ID_QUERY, note_id=note_id, note_type=str(note_type))
        record = await result.single()
        if record is None:
            return
        await touch_edition(tx, record["edition_id"])
        await tx.run(NoteDatabase.DELETE_QUERY, note_id=note_id, note_type=str(note_type))

    async def delete(self, note_id: str, note_type: NoteType = NoteType.DURCHEN) -> None:
        async with self._db.get_session() as session:
            await session.execute_write(NoteDatabase.delete_with_transaction, note_id, note_type)

    async def delete_all_footnotes(self, edition_id: str) -> None:
        async def write(tx: AsyncManagedTransaction) -> None:
            await touch_edition(tx, edition_id)  # raises DataNotFoundError for an unknown edition
            await tx.run(NoteDatabase.DELETE_ALL_BY_TYPE_QUERY, edition_id=edition_id, note_type=str(NoteType.FOOTNOTE))

        async with self._db.get_session() as session:
            await session.execute_write(write)
