from typing import TYPE_CHECKING, LiteralString

from database.annotation import bibliographic, mark, note, table_of_contents
from database.annotation import pagination as pagination_database
from database.annotation import segmentation as segmentation_database
from errors import DataConflictError, DataValidationError
from models.edition import EditionOutput
from models.enums import EditionType
from storage import edition_key

from . import excerpt, nomen, span
from . import text as text_database
from .content_state import ContentState, advance_revision, lock_connected_editions, read_state
from .locking import lock_nodes
from .repository import SessionSource, create_id, fetch_all, query_all, query_one, reading, writing
from .schema import SCHEMA

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction

    from models.annotation import PaginationInput, SegmentationInput
    from models.edition import EditionInput, ExcerptSource
    from models.text import TextInput

DELETE_QUERY: LiteralString = f"""
MATCH (m:Edition {{id: $edition_id}})
{nomen.delete_names("m", "HAS_INCIPIT_TITLE")}
OPTIONAL MATCH (attribute:Attribute)-[:ATTRIBUTE_OF]->(m)
OPTIONAL MATCH (span:Span)-[:SPAN_OF]->(attribute)
OPTIONAL MATCH (attribute)-[:HAS_METADATA]->(metadata:AnnotationMetadata)
DETACH DELETE m, span, metadata, attribute
FINISH
"""
_EDITION_RETURN: LiteralString = f"""
RETURN {{
    id: m.id, bdrc: m.bdrc, wiki: m.wiki, colophon: m.colophon,
    source: [(m)-[:HAS_SOURCE]->(s:Source) | s.name][0],
    type: [(m)-[:HAS_TYPE]->(mt:EditionType) | mt.name][0],
    incipit_title: {nomen.optional_localized_map("(m)-[:HAS_INCIPIT_TITLE]->(:Nomen)")},
    alt_incipit_titles: nullIf({nomen.alternative_maps("(m)-[:HAS_INCIPIT_TITLE]->(:Nomen)")}, []),
    excerpt_of: [(m)-[:EXCERPT_OF]->(source:Edition) | {{
        edition_id: source.id,
        spans: COLLECT {{
            MATCH (span:Span)-[:SPAN_OF]->(m)
            WITH span ORDER BY span.index
            RETURN {{start: span.start, end: span.end}}
        }}
    }}][0],
    text_id: e.id
}} as edition
"""
GET_BY_ID_QUERY: LiteralString = f"""
MATCH (m:Edition {{id: $edition_id}})-[:EDITION_OF]->(e:Text)
{_EDITION_RETURN}
"""
GET_BY_TEXT_ID_QUERY: LiteralString = f"""
MATCH (m:Edition)-[:EDITION_OF]->(e:Text {{id: $text_id}})
WHERE $edition_type IS NULL OR EXISTS {{ (m)-[:HAS_TYPE]->(:EditionType {{name: $edition_type}}) }}
{_EDITION_RETURN}
"""
GET_RELATED_QUERY: LiteralString = f"""
CALL () {{
    MATCH (:Edition {{id: $edition_id}})
          -[:HAS_SEGMENTATION]->(:Segmentation)
          <-[:SEGMENT_OF]-(:Segment)
          -[:ALIGNED_TO]-(:Segment)
          -[:SEGMENT_OF]->(:Segmentation)
          <-[:HAS_SEGMENTATION]-(m:Edition)
    WHERE m.id <> $edition_id
    RETURN m
    UNION
    MATCH (:Edition {{id: $edition_id}})-[:EDITION_OF]->(:Text)
          -[:TRANSLATION_OF|:COMMENTARY_OF]-(:Text)<-[:EDITION_OF]-(m:Edition)
    WHERE m.id <> $edition_id
    RETURN m
}}
MATCH (m)-[:EDITION_OF]->(e:Text)
WITH DISTINCT m, e
{_EDITION_RETURN}
"""
CHECK_CREATE_QUERY: LiteralString = """
MATCH (t:Text {id: $text_id})
RETURN $critical AND EXISTS {
    (t)<-[:EDITION_OF]-(:Edition)-[:HAS_TYPE]->(:EditionType {name: 'critical'})
} AS duplicate_critical
"""
CREATE_QUERY: LiteralString = """
MATCH (e:Text {id: $text_id})
OPTIONAL MATCH (it:Nomen {id: $incipit_nomen_id})
MERGE (mt:EditionType {name: $type})
CREATE (m:Edition {
    id: $edition_id, bdrc: $bdrc, wiki: $wiki, colophon: $colophon, content_length: $content_length,
    content_key: $content_key, revision: 0
})
WITH m, e, mt, it
CREATE (m)-[:EDITION_OF]->(e), (m)-[:HAS_TYPE]->(mt)
CALL (*) { WHEN it IS NOT NULL THEN { CREATE (m)-[:HAS_INCIPIT_TITLE]->(it) } }
CALL (*) { WHEN $source IS NOT NULL THEN { MERGE (s:Source {name: $source}) CREATE (m)-[:HAS_SOURCE]->(s) } }
RETURN m.id AS edition_id
"""


@reading
async def get_all(
    tx: AsyncManagedTransaction, text_id: str, edition_type: EditionType | None = None
) -> list[EditionOutput]:
    return await fetch_all(
        tx,
        GET_BY_TEXT_ID_QUERY,
        EditionOutput,
        field="edition",
        text_id=text_id,
        edition_type=edition_type.value if edition_type else None,
    )


@writing
async def delete(tx: AsyncManagedTransaction, edition_id: str) -> None:
    await advance_revision(tx, edition_id)
    result = await tx.run("RETURN count { (:Recording)-[:FOLLOWS]->(:Edition {id: $id}) } AS followers", id=edition_id)
    if followers := (await result.single(strict=True))["followers"]:
        raise DataConflictError(f"Edition '{edition_id}' cannot be deleted while {followers} recording(s) follow it")
    if (await read_state(tx, edition_id)).is_excerpt:
        await tx.run(excerpt.DELETE_QUERY, edition_id=edition_id)
    elif users := (await excerpt.excerpt_editions_of(tx, edition_id)):
        raise DataConflictError(
            f"Edition '{edition_id}' cannot be deleted because excerpt edition(s) {', '.join(users)} use it"
        )
    else:
        await segmentation_database.delete_by_edition(tx, edition_id)
    for query in (
        pagination_database.DELETE_ALL_QUERY,
        table_of_contents.DELETE_ALL_QUERY,
        bibliographic.DELETE_ALL_QUERY,
        note.DELETE_ALL_QUERY,
        mark.DELETE_ALL_QUERY,
        DELETE_QUERY,
    ):
        await tx.run(query, edition_id=edition_id)


async def _create(
    tx: AsyncManagedTransaction,
    edition: EditionInput,
    text_id: str,
    edition_id: str,
    content_length: int,
    *,
    content_key: str | None = None,
) -> str:
    await lock_nodes(tx, "Text", [text_id])
    result = await tx.run(CHECK_CREATE_QUERY, text_id=text_id, critical=edition.type is EditionType.CRITICAL)
    record = await result.single()
    if record is None:
        raise DataValidationError(f"Text {text_id} does not exist. Cannot create edition for non-existent text.")
    if record["duplicate_critical"]:
        raise DataValidationError(SCHEMA["relationship_templates"]["critical_edition_per_text"]["description"])
    incipit_nomen_id = (
        await nomen.create(tx, edition.incipit_title.root, [alt.root for alt in edition.alt_incipit_titles or []])
        if edition.incipit_title
        else None
    )
    return await create_id(
        tx,
        CREATE_QUERY,
        "edition_id",
        edition_id=edition_id,
        text_id=text_id,
        bdrc=edition.bdrc,
        wiki=edition.wiki,
        type=edition.type.value,
        colophon=edition.colophon,
        incipit_nomen_id=incipit_nomen_id,
        source=edition.source,
        content_length=content_length,
        content_key=None if edition.type is EditionType.EXCERPT else content_key or edition_key(text_id, edition_id),
    )


async def get(db: SessionSource, edition_id: str) -> EditionOutput:
    return await query_one(
        db,
        GET_BY_ID_QUERY,
        EditionOutput,
        f"Edition with ID '{edition_id}' not found",
        field="edition",
        edition_id=edition_id,
    )


async def get_related(db: SessionSource, edition_id: str) -> list[EditionOutput]:
    """Find all editions related through alignment or text relationships."""
    return await query_all(db, GET_RELATED_QUERY, EditionOutput, field="edition", edition_id=edition_id)


@writing
async def commit_content_edit(
    tx: AsyncManagedTransaction,
    previous: ContentState,
    *,
    content_key: str,
    content_length: int,
    span_edit: tuple[int, int, int] | None,
) -> None:
    """Publish prepared content. A changed span carries its old start, old end and replacement length."""
    edition_id = previous.edition_id
    related = await lock_connected_editions(tx, edition_id)
    current = await read_state(tx, edition_id)
    if current.revision != previous.revision:
        raise DataConflictError(f"Edition changed: expected revision {previous.revision}, current {current.revision}")
    if span_edit is not None:
        await span.adjust_spans(tx, edition_id, *span_edit)
        await excerpt.propagate_edit(tx, edition_id, *span_edit)
    await tx.run(
        """
        MATCH (e:Edition {id: $id})
        SET e.content_key = $key, e.content_length = $length, e.revision = e.revision + 1
        """,
        id=edition_id,
        key=content_key,
        length=content_length,
    )
    for related_id in related:
        await advance_revision(tx, related_id)


@writing
async def create(
    tx: AsyncManagedTransaction,
    edition: EditionInput,
    edition_id: str,
    text_id: str,
    content_length: int,
    *,
    text: TextInput | None = None,
    pagination: PaginationInput | None = None,
    segmentation: SegmentationInput | None = None,
    content_key: str | None = None,
    excerpt_of: ExcerptSource | None = None,
) -> str:
    if text:
        await text_database.create_in_transaction(tx, text, text_id)
    created_edition_id = await _create(tx, edition, text_id, edition_id, content_length, content_key=content_key)
    if excerpt_of is not None:
        await excerpt.add(tx, created_edition_id, text_id, excerpt_of)
    if segmentation is not None:
        await segmentation_database.add(tx, created_edition_id, segmentation)
    if pagination is not None:
        await pagination_database.add(tx, created_edition_id, pagination)
    return created_edition_id
