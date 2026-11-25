"""Verify legacy content and backfill pointers during an explicit maintenance pause."""

from typing import TYPE_CHECKING, LiteralString

from errors import DataValidationError
from storage import edition_key

from .repository import iter_id_batches

if TYPE_CHECKING:
    from database import Database
    from storage import Storage

EDITIONS_QUERY: LiteralString = """
    MATCH (e:Edition)-[:EDITION_OF]->(t:Text)
    WHERE e.id > $after AND NOT (e)-[:HAS_TYPE]->(:EditionType {name: 'excerpt'})
    RETURN e.id AS id, t.id AS text_id, e.content_key AS key, e.content_length AS length, e.revision AS revision
"""

INVALID_SPANS_QUERY: LiteralString = """
    MATCH (e:Edition {id: $id})
    CALL (e) {
        MATCH (e)-[:HAS_SEGMENTATION]->()<-[:SEGMENT_OF]-()<-[:SPAN_OF]-(s:Span) RETURN s
        UNION MATCH (e)<-[:PAGINATION_OF]-()<-[:VOLUME_OF]-()<-[:PAGE_OF]-()<-[:SPAN_OF]-(s:Span) RETURN s
        UNION MATCH (e)<-[:NOTE_OF|MARK_OF|BIBLIOGRAPHY_OF|ATTRIBUTE_OF]-()<-[:SPAN_OF]-(s:Span) RETURN s
        UNION MATCH (e)<-[:TOC_OF]-()<-[:SECTION_OF]-()<-[:SPAN_OF]-(s:Span) RETURN s
        UNION MATCH (e)<-[:RECORDING_OF|FOLLOWS]-()<-[:CUE_OF]-()<-[:SPAN_OF]-(s:Span) RETURN s
    }
    WITH s
    WHERE s.start IS NULL OR s.end IS NULL OR s.start < 0 OR s.end < s.start OR s.end > $length
    RETURN count(s) AS invalid
"""

BACKFILL_QUERY: LiteralString = """
    MATCH (e:Edition {id: $id})
    SET e.content_key = coalesce(e.content_key, $key),
        e.content_length = coalesce(e.content_length, $length),
        e.revision = coalesce(e.revision, 0)
"""


async def migrate_content(db: Database, storage: Storage, *, apply: bool = False) -> int:
    """Backfill only missing fields; existing objects can remain at their legacy keys."""
    count = 0
    async for editions in iter_id_batches(db, EDITIONS_QUERY):
        for edition in editions:
            key = edition["key"] or edition_key(edition["text_id"], edition["id"])
            length = len(await storage.read_text(key))
            if not length or edition["length"] not in (None, length):
                raise DataValidationError(f"Edition {edition['id']} has empty content or a length mismatch")
            [invalid] = await db.query(INVALID_SPANS_QUERY, id=edition["id"], length=length)
            if invalid["invalid"]:
                raise DataValidationError(f"Edition {edition['id']} has annotations outside its actual content")
            if apply and any(edition[field] is None for field in ("key", "revision", "length")):
                await db.query(BACKFILL_QUERY, write=True, id=edition["id"], key=key, length=length)
            count += 1
    return count
