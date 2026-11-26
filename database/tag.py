from typing import TYPE_CHECKING, Literal, LiteralString

from errors import DataConflictError, DataNotFoundError, DataValidationError, ForbiddenError
from identifier import generate_id
from models.tag import TagOutput

from . import nomen
from .locking import lock_nodes
from .repository import SessionSource, create_id, fetch_record, query_all, require_node, writing

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction

    from models.tag import TagInput


def find_existing_title_query(label: LiteralString, scope: LiteralString = "") -> LiteralString:
    """Find one of $titles already used, case-insensitively, by another node of the label in $application."""
    return f"""
        MATCH (app:Application {{id: $application}})
        UNWIND $titles AS title
        MATCH (n:{label})-[:BELONGS_TO]->(app)
        {scope}
        MATCH (n)-[:HAS_TITLE]->(:Nomen)-[:HAS_LOCALIZATION]->(lt:LocalizedText)
            -[r:HAS_LANGUAGE]->(lang:Language)
        WHERE toLower(coalesce(r.bcp47, lang.code)) = toLower(title.language)
          AND toLower(lt.text) = toLower(title.text)
        RETURN title.language AS language, title.text AS title_text
        LIMIT 1
    """


async def validate_unique_title(
    tx: AsyncManagedTransaction,
    label: LiteralString,
    query: LiteralString,
    application: str,
    title: dict[str, str],
    **params: str | None,
) -> None:
    """Serialize on the application, so two concurrent creates cannot both pass the check."""
    await lock_nodes(tx, "Application", [application])
    result = await tx.run(
        query,
        params,
        application=application,
        titles=[{"language": language, "text": title_text} for language, title_text in title.items()],
    )
    if record := (await result.single()):
        raise DataValidationError(
            f"{label} with title '{record['title_text']}' in language '{record['language']}' "
            f"already exists for application '{application}'"
        )


GET_ALL_QUERY: LiteralString = f"""
MATCH (t:Tag)-[:BELONGS_TO]->(:Application {{id: $application}})
RETURN {{
    id: t.id,
    title: {nomen.localized_map("(t)-[:HAS_TITLE]->(:Nomen)")},
    description: {nomen.optional_localized_map("(t)-[:HAS_DESCRIPTION]->(:Nomen)")}
}} AS tag
"""
CREATE_QUERY: LiteralString = """
    MATCH (n:Nomen {id: $nomen_id})
    MATCH (app:Application {id: $application})
    CREATE (t:Tag {id: $tag_id})
    CREATE (t)-[:HAS_TITLE]->(n)
    CREATE (t)-[:BELONGS_TO]->(app)
    WITH t
    OPTIONAL MATCH (desc_nomen:Nomen {id: $description_nomen_id})
    WITH t, desc_nomen
    CALL (*) { WHEN desc_nomen IS NOT NULL THEN { CREATE (t)-[:HAS_DESCRIPTION]->(desc_nomen) } }
    RETURN t.id AS tag_id
"""
FIND_EXISTING_QUERY: LiteralString = find_existing_title_query("Tag")
DELETE_QUERY: LiteralString = f"""
    MATCH (t:Tag {{id: $tag_id}})-[:BELONGS_TO]->(app:Application {{id: $application}})
    WITH t, t.id AS deleted_tag_id
    {nomen.delete_names("t", "HAS_TITLE", "HAS_DESCRIPTION")}
    DETACH DELETE t
    RETURN deleted_tag_id
"""
SET_WORK_TAGS_QUERY: LiteralString = """
    MATCH (w:Work {id: $work_id})
    OPTIONAL MATCH (w)-[old:HAS_TAG]->(tag:Tag)
    WHERE $replace AND ($application IS NULL OR
        (tag)-[:BELONGS_TO]->(:Application {id: $application}))
    DELETE old
    WITH DISTINCT w
    UNWIND $tag_ids AS tag_id
    MATCH (tag:Tag {id: tag_id})
    MERGE (w)-[:HAS_TAG]->(tag)
"""


async def set_work_tags(
    tx: AsyncManagedTransaction,
    work_id: str,
    tag_ids: list[str],
    *,
    application: str | None = None,
    replace: bool = False,
) -> None:
    """Caller created the Work in this transaction or already holds its write lock."""
    await lock_nodes(tx, "Tag", tag_ids)
    await validate_tags_exist(tx, tag_ids, application)
    await tx.run(SET_WORK_TAGS_QUERY, work_id=work_id, tag_ids=tag_ids, application=application, replace=replace)


async def _validate_not_exists(tx: AsyncManagedTransaction, application: str, title: dict[str, str]) -> None:
    await validate_unique_title(tx, "Tag", FIND_EXISTING_QUERY, application, title)


async def validate_tags_exist(tx: AsyncManagedTransaction, tag_ids: list[str], application: str | None = None) -> None:
    """Validate that all given tag IDs exist. Raises DataValidationError listing missing IDs."""
    if not tag_ids:
        return
    query = """
    MATCH (t:Tag) WHERE t.id IN $tag_ids
    WITH collect(DISTINCT t) AS tags
    RETURN [x IN $tag_ids WHERE NOT x IN [t IN tags | t.id]] AS missing_tags,
           [t IN tags WHERE $application IS NOT NULL AND NOT
               (t)-[:BELONGS_TO]->(:Application {id: $application}) | t.id] AS foreign_tags
    """
    result = await tx.run(query, tag_ids=tag_ids, application=application)
    record = await result.single(strict=True)
    if record["missing_tags"]:
        raise DataValidationError(f"Referenced tags do not exist: {', '.join(record['missing_tags'])}")
    if record["foreign_tags"]:
        raise ForbiddenError("Tags belong to another application")


async def get_all(db: SessionSource, application: str) -> list[TagOutput]:
    return await query_all(db, GET_ALL_QUERY, TagOutput, field="tag", application=application)


@writing
async def create(tx: AsyncManagedTransaction, tag: TagInput, application: str) -> str:
    await _validate_not_exists(tx, application, tag.title.root)
    tag_id = generate_id()
    nomen_id = await nomen.create(tx, tag.title.root)
    description = tag.description.root if tag.description else None
    description_nomen_id = await nomen.create_optional(tx, description)
    return await create_id(
        tx,
        CREATE_QUERY,
        "tag_id",
        tag_id=tag_id,
        application=application,
        nomen_id=nomen_id,
        description_nomen_id=description_nomen_id,
    )


@writing
async def delete(tx: AsyncManagedTransaction, tag_id: str, application: str) -> list[str]:

    async def work_ids() -> set[str]:
        result = await tx.run("MATCH (:Tag {id: $id})<-[:HAS_TAG]-(w:Work) RETURN w.id AS id", id=tag_id)
        return set(await result.value("id"))

    works = await work_ids()
    await lock_nodes(tx, "Work", sorted(works))
    await lock_nodes(tx, "Tag", [tag_id])
    if not await work_ids() <= works:
        raise DataConflictError("Tag attachments changed while acquiring locks; retry the operation")
    not_found = f"Tag with ID '{tag_id}' not found in application '{application}'"
    await fetch_record(tx, DELETE_QUERY, not_found, tag_id=tag_id, application=application)
    return sorted(works)


async def _set_tag(
    tx: AsyncManagedTransaction,
    owner: Literal["Work", "Segment"],
    owner_id: str,
    tag_id: str,
    application: str | None,
    *,
    attach: bool,
) -> None:
    await lock_nodes(tx, owner, [owner_id])
    await lock_nodes(tx, "Tag", [tag_id])
    mutation = "MERGE (owner)-[:HAS_TAG]->(tag)" if attach else "MATCH (owner)-[rel:HAS_TAG]->(tag) DELETE rel"
    result = await tx.run(
        f"""
        MATCH (owner:{owner} {{id: $owner_id}})
        MATCH (tag:Tag {{id: $tag_id}})
        WHERE $application IS NULL OR (tag)-[:BELONGS_TO]->(:Application {{id: $application}})
        {mutation}
        RETURN owner.id
        """,
        owner_id=owner_id,
        tag_id=tag_id,
        application=application,
    )
    if await result.single():
        return
    if not attach:
        raise DataNotFoundError(f"Tag '{tag_id}' is not attached to {owner} '{owner_id}'")
    await require_node(tx, owner, owner_id, DataNotFoundError(f"{owner} '{owner_id}' not found"))
    scope = f" in application '{application}'" if application is not None else ""
    raise DataNotFoundError(f"Tag '{tag_id}' not found{scope}")


@writing
async def tag_work(tx: AsyncManagedTransaction, work_id: str, tag_id: str, application: str | None = None) -> None:
    await _set_tag(tx, "Work", work_id, tag_id, application, attach=True)


@writing
async def untag_work(tx: AsyncManagedTransaction, work_id: str, tag_id: str, application: str | None = None) -> None:
    await _set_tag(tx, "Work", work_id, tag_id, application, attach=False)


@writing
async def tag_segment(
    tx: AsyncManagedTransaction, segment_id: str, tag_id: str, application: str | None = None
) -> None:
    await _set_tag(tx, "Segment", segment_id, tag_id, application, attach=True)


@writing
async def untag_segment(
    tx: AsyncManagedTransaction, segment_id: str, tag_id: str, application: str | None = None
) -> None:
    await _set_tag(tx, "Segment", segment_id, tag_id, application, attach=False)
