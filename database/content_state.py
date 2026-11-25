"""Edition revisions and consistent reads shared by content and annotation writers."""

from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from typing import LiteralString

from neo4j import AsyncManagedTransaction

from database.locking import lock_nodes
from exceptions import DataConflictError, DataNotFoundError, DataValidationError


@dataclass(frozen=True)
class ContentState:
    edition_id: str
    text_id: str
    object_key: str | None
    length: int
    revision: int
    is_excerpt: bool

    @property
    def stored_key(self) -> str:
        if self.object_key is None:
            raise DataConflictError(
                f"Edition '{self.edition_id}' is an excerpt edition and stores no content; use its excerpt_of edition"
            )
        return self.object_key

    def validate_span(self, end: int) -> None:
        if end > self.length:
            raise DataValidationError(
                f"Offsets extend to {end} but edition '{self.edition_id}' content is {self.length} characters; "
                "offsets must be Unicode code point positions in the edition content"
            )


async def read_state(tx: AsyncManagedTransaction, edition_id: str) -> ContentState:
    result = await tx.run(
        """
        MATCH (e:Edition {id: $id})-[:EDITION_OF]->(text:Text)
        RETURN e.id AS edition_id, text.id AS text_id, e.content_key AS object_key,
               e.content_length AS length, e.revision AS revision,
               EXISTS { (e)-[:HAS_TYPE]->(:EditionType {name: 'excerpt'}) } AS is_excerpt
    """,
        id=edition_id,
    )
    record = await result.single()
    if record is None:
        raise DataNotFoundError(f"Edition with ID '{edition_id}' not found")
    if None in (record["length"], record["revision"]) or (record["object_key"] is None) != record["is_excerpt"]:
        raise DataValidationError(f"Edition '{edition_id}' requires the content-state migration")
    return ContentState(**record)


async def read_revisions(tx: AsyncManagedTransaction, edition_ids: Collection[str]) -> dict[str, int]:
    result = await tx.run(
        "MATCH (e:Edition) WHERE e.id IN $ids RETURN e.id AS id, e.revision AS revision", ids=list(edition_ids)
    )
    return {record["id"]: record["revision"] async for record in result}


async def advance_revision(tx: AsyncManagedTransaction, edition_id: str) -> None:
    """Neo4j locks before reading the revision and holds that lock until commit."""
    result = await tx.run(
        """
        MATCH (e:Edition {id: $id}) SET e.revision = e.revision + 1
        FINISH
    """,
        id=edition_id,
    )
    await result.consume()


async def read_at_revision[T](
    tx: AsyncManagedTransaction, edition_id: str, read: Callable[[AsyncManagedTransaction], Awaitable[T]]
) -> tuple[ContentState, T]:
    """No long-lived read lock: reject a graph view that overlaps a committed mutation."""
    for _ in range(3):
        state = await read_state(tx, edition_id)
        value = await read(tx)
        if state == await read_state(tx, edition_id):
            return state, value
    raise DataConflictError("Edition changed while reading; reload and retry")


async def touch_edition(tx: AsyncManagedTransaction, edition_id: str) -> ContentState:
    """Serialize an annotation mutation and invalidate edits prepared before it."""
    await advance_revision(tx, edition_id)
    state = await read_state(tx, edition_id)
    if state.is_excerpt:
        raise DataConflictError(
            f"Edition '{edition_id}' is an excerpt edition and has no annotations of its own; "
            "annotate its excerpt_of edition instead"
        )
    return state


async def touch_annotation(tx: AsyncManagedTransaction, label: LiteralString, annotation_id: str) -> None:
    result = await tx.run(f"MATCH (a:{label} {{id: $id}})-->(e:Edition) RETURN e.id AS id", id=annotation_id)
    record = await result.single()
    if record is not None:
        await touch_edition(tx, record["id"])


async def lock_connected_editions(tx: AsyncManagedTransaction, edition_id: str) -> list[str]:
    """Content and segment changes alter the alignment views on both ends and the excerpt editions using it."""

    async def connected() -> set[str]:
        result = await tx.run(
            """
            MATCH (:Edition {id: $id})-[:HAS_SEGMENTATION]->(:Segmentation)<-[:SEGMENT_OF]-(:Segment)
                  -[:ALIGNED_TO]-(:Segment)-[:SEGMENT_OF]->(:Segmentation)<-[:HAS_SEGMENTATION]-(e:Edition)
            RETURN e.id AS id
            UNION
            MATCH (:Edition {id: $id})<-[:EXCERPT_OF]-(e:Edition)
            RETURN e.id AS id
        """,
            id=edition_id,
        )
        return {edition_id, *await result.value("id")}

    ids = await connected()
    await lock_nodes(tx, "Edition", sorted(ids))
    if not await connected() <= ids:
        raise DataConflictError("Alignment changed while acquiring edition locks; retry the operation")
    return sorted(ids - {edition_id})


async def read_value[T](
    tx: AsyncManagedTransaction, edition_id: str, read: Callable[[AsyncManagedTransaction], Awaitable[T]]
) -> T:
    return (await read_at_revision(tx, edition_id, read))[1]
