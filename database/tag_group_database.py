from typing import TYPE_CHECKING, LiteralString

from exceptions import DataConflictError, DataNotFoundError, DataValidationError
from identifier import generate_id
from models.tag import TagGroupOutput

from .locking import lock_nodes
from .nomen_database import NomenDatabase

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction, AsyncSession

    from models.tag import TagGroupInput, TagGroupPatch

    from .database import Database


class TagGroupDatabase:
    """Application-scoped groups of tags. Text filters OR the tags inside a group and AND across groups."""

    _RETURN: LiteralString = """
    RETURN {
        id: g.id,
        title: apoc.map.fromPairs([(g)-[:HAS_TITLE]->(n:Nomen)-[:HAS_LOCALIZATION]->(lt:LocalizedText)
            -[r:HAS_LANGUAGE]->(l:Language) | [coalesce(r.bcp47, l.code), lt.text]]),
        description: CASE WHEN EXISTS {
            (g)-[:HAS_DESCRIPTION]->(:Nomen)-[:HAS_LOCALIZATION]->(:LocalizedText)
        } THEN apoc.map.fromPairs([(g)-[:HAS_DESCRIPTION]->(dn:Nomen)-[:HAS_LOCALIZATION]->(dlt:LocalizedText)
            -[r:HAS_LANGUAGE]->(dl:Language) | [coalesce(r.bcp47, dl.code), dlt.text]]) ELSE null END,
        tag_ids: [(t:Tag)-[:IN_GROUP]->(g) | t.id]
    } AS tag_group
    """

    GET_ALL_QUERY: LiteralString = f"""
    MATCH (g:TagGroup)-[:BELONGS_TO]->(:Application {{id: $application}})
    {_RETURN}
    """

    GET_QUERY: LiteralString = f"""
    MATCH (g:TagGroup {{id: $group_id}})-[:BELONGS_TO]->(:Application {{id: $application}})
    {_RETURN}
    """

    # Swap a group's title or description Nomen for one holding the sent localizations.
    REPLACE_TITLE_QUERY: LiteralString = """
        MATCH (g:TagGroup {id: $group_id})
        OPTIONAL MATCH (g)-[:HAS_TITLE]->(old:Nomen)
        OPTIONAL MATCH (old)-[:HAS_LOCALIZATION]->(old_lt:LocalizedText)
        DETACH DELETE old, old_lt
        WITH DISTINCT g
        MATCH (new:Nomen {id: $nomen_id})
        CREATE (g)-[:HAS_TITLE]->(new)
    """

    REPLACE_DESCRIPTION_QUERY: LiteralString = """
        MATCH (g:TagGroup {id: $group_id})
        OPTIONAL MATCH (g)-[:HAS_DESCRIPTION]->(old:Nomen)
        OPTIONAL MATCH (old)-[:HAS_LOCALIZATION]->(old_lt:LocalizedText)
        DETACH DELETE old, old_lt
        WITH DISTINCT g
        MATCH (new:Nomen {id: $nomen_id})
        CREATE (g)-[:HAS_DESCRIPTION]->(new)
    """

    CREATE_QUERY: LiteralString = """
        MATCH (n:Nomen {id: $nomen_id})
        MATCH (app:Application {id: $application})
        CREATE (g:TagGroup {id: $group_id})
        CREATE (g)-[:HAS_TITLE]->(n)
        CREATE (g)-[:BELONGS_TO]->(app)
        WITH g
        OPTIONAL MATCH (desc_nomen:Nomen {id: $description_nomen_id})
        WITH g, desc_nomen
        CALL (*) { WHEN desc_nomen IS NOT NULL THEN { CREATE (g)-[:HAS_DESCRIPTION]->(desc_nomen) } }
        RETURN g.id AS group_id
    """

    FIND_EXISTING_QUERY: LiteralString = """
        UNWIND $titles AS title
        MATCH (g:TagGroup)-[:BELONGS_TO]->(:Application {id: $application})
        WHERE $exclude_group_id IS NULL OR g.id <> $exclude_group_id
        MATCH (g)-[:HAS_TITLE]->(:Nomen)-[:HAS_LOCALIZATION]->(lt:LocalizedText)
            -[r:HAS_LANGUAGE]->(lang:Language)
        WHERE toLower(coalesce(r.bcp47, lang.code)) = toLower(title.language)
          AND toLower(lt.text) = toLower(title.text)
        RETURN title.language AS language, title.text AS title_text
        LIMIT 1
    """

    GET_FOR_DELETE_QUERY: LiteralString = """
        MATCH (g:TagGroup {id: $group_id})-[:BELONGS_TO]->(:Application {id: $application})
        RETURN count { (:Tag)-[:IN_GROUP]->(g) } AS tag_count
    """

    DELETE_QUERY: LiteralString = """
        MATCH (g:TagGroup {id: $group_id})-[:BELONGS_TO]->(:Application {id: $application})
        OPTIONAL MATCH (g)-[:HAS_TITLE]->(title_nomen:Nomen)
        OPTIONAL MATCH (title_nomen)-[:HAS_LOCALIZATION]->(title_lt:LocalizedText)
        OPTIONAL MATCH (g)-[:HAS_DESCRIPTION]->(desc_nomen:Nomen)
        OPTIONAL MATCH (desc_nomen)-[:HAS_LOCALIZATION]->(desc_lt:LocalizedText)
        DETACH DELETE g, title_nomen, title_lt, desc_nomen, desc_lt
    """

    # A tag belongs to at most one group, so adding it to a group moves it out of any previous one.
    ADD_TAG_QUERY: LiteralString = """
        MATCH (app:Application {id: $application})
        MATCH (t:Tag {id: $tag_id})-[:BELONGS_TO]->(app)
        MATCH (g:TagGroup {id: $group_id})-[:BELONGS_TO]->(app)
        OPTIONAL MATCH (t)-[old:IN_GROUP]->(:TagGroup)
        DELETE old
        WITH DISTINCT t, g
        MERGE (t)-[:IN_GROUP]->(g)
        RETURN t.id AS tag_id
    """

    GROUP_EXISTS_QUERY: LiteralString = """
        MATCH (g:TagGroup {id: $group_id})-[:BELONGS_TO]->(:Application {id: $application})
        RETURN g.id AS group_id
    """

    REMOVE_TAG_QUERY: LiteralString = """
        MATCH (t:Tag {id: $tag_id})-[r:IN_GROUP]->(:TagGroup {id: $group_id})
            -[:BELONGS_TO]->(:Application {id: $application})
        DELETE r
        RETURN t.id AS tag_id
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def session(self) -> AsyncSession:
        return self._db.get_session()

    async def get_all(self, application: str) -> list[TagGroupOutput]:
        async def read(tx: AsyncManagedTransaction) -> list[TagGroupOutput]:
            result = await tx.run(TagGroupDatabase.GET_ALL_QUERY, application=application)
            return [TagGroupOutput.model_validate(record["tag_group"]) for record in await result.data()]

        async with self.session as session:
            return await session.execute_read(read)

    @staticmethod
    async def _validate_title_available(
        tx: AsyncManagedTransaction, application: str, title: dict[str, str], exclude_group_id: str | None = None
    ) -> None:
        """Reject a title another group of the application already uses. Callers hold the Application lock."""
        result = await tx.run(
            TagGroupDatabase.FIND_EXISTING_QUERY,
            application=application,
            exclude_group_id=exclude_group_id,
            titles=[{"language": language, "text": text} for language, text in title.items()],
        )
        if existing := await result.single():
            raise DataValidationError(
                f"Tag group with title '{existing['title_text']}' in language '{existing['language']}' "
                f"already exists for application '{application}'"
            )

    @staticmethod
    async def _get_with_transaction(tx: AsyncManagedTransaction, group_id: str, application: str) -> TagGroupOutput:
        result = await tx.run(TagGroupDatabase.GET_QUERY, group_id=group_id, application=application)
        record = await result.single()
        if record is None:
            raise DataNotFoundError(f"Tag group '{group_id}' not found in application '{application}'")
        return TagGroupOutput.model_validate(record["tag_group"])

    async def create(self, group: TagGroupInput, application: str) -> str:
        async def write(tx: AsyncManagedTransaction) -> str:
            await lock_nodes(tx, "Application", [application])
            await TagGroupDatabase._validate_title_available(tx, application, group.title.root)

            nomen_id = await NomenDatabase.create_with_transaction(tx, group.title.root, None)
            description_nomen_id = None
            if group.description is not None:
                description_nomen_id = await NomenDatabase.create_with_transaction(tx, group.description.root, None)

            result = await tx.run(
                TagGroupDatabase.CREATE_QUERY,
                group_id=generate_id(),
                application=application,
                nomen_id=nomen_id,
                description_nomen_id=description_nomen_id,
            )
            record = await result.single(strict=True)
            return record["group_id"]

        async with self.session as session:
            return str(await session.execute_write(write))

    async def update(self, group_id: str, patch: TagGroupPatch, application: str) -> TagGroupOutput:
        async def write(tx: AsyncManagedTransaction) -> TagGroupOutput:
            # Application first, as in create, so title checks of concurrent creates and renames serialize.
            await lock_nodes(tx, "Application", [application])
            await lock_nodes(tx, "TagGroup", [group_id])
            await TagGroupDatabase._get_with_transaction(tx, group_id, application)

            # Like other PATCH endpoints, a sent localization map replaces the field as a whole.
            if patch.title is not None:
                await TagGroupDatabase._validate_title_available(tx, application, patch.title.root, group_id)
                nomen_id = await NomenDatabase.create_with_transaction(tx, patch.title.root, None)
                await (
                    await tx.run(TagGroupDatabase.REPLACE_TITLE_QUERY, group_id=group_id, nomen_id=nomen_id)
                ).consume()
            if patch.description is not None:
                nomen_id = await NomenDatabase.create_with_transaction(tx, patch.description.root, None)
                await (
                    await tx.run(TagGroupDatabase.REPLACE_DESCRIPTION_QUERY, group_id=group_id, nomen_id=nomen_id)
                ).consume()
            return await TagGroupDatabase._get_with_transaction(tx, group_id, application)

        async with self.session as session:
            return await session.execute_write(write)

    async def delete(self, group_id: str, application: str) -> None:
        """Delete an empty group. Groups that still hold tags are rejected, since deleting one would silently
        change how text filters combine those tags."""

        async def write(tx: AsyncManagedTransaction) -> None:
            await lock_nodes(tx, "TagGroup", [group_id])
            result = await tx.run(TagGroupDatabase.GET_FOR_DELETE_QUERY, group_id=group_id, application=application)
            record = await result.single()
            if record is None:
                raise DataNotFoundError(f"Tag group '{group_id}' not found in application '{application}'")
            if record["tag_count"]:
                raise DataConflictError(
                    f"Tag group '{group_id}' still has {record['tag_count']} tag(s); remove them before deleting it"
                )
            await (await tx.run(TagGroupDatabase.DELETE_QUERY, group_id=group_id, application=application)).consume()

        async with self.session as session:
            await session.execute_write(write)

    async def add_tag(self, group_id: str, tag_id: str, application: str) -> None:
        async def write(tx: AsyncManagedTransaction) -> None:
            # Tag before TagGroup, matching every other writer, so concurrent moves cannot deadlock.
            await lock_nodes(tx, "Tag", [tag_id])
            await lock_nodes(tx, "TagGroup", [group_id])
            params = {"group_id": group_id, "tag_id": tag_id, "application": application}
            if await (await tx.run(TagGroupDatabase.ADD_TAG_QUERY, **params)).single():
                return
            if await (await tx.run(TagGroupDatabase.GROUP_EXISTS_QUERY, **params)).single() is None:
                raise DataNotFoundError(f"Tag group '{group_id}' not found in application '{application}'")
            raise DataNotFoundError(f"Tag '{tag_id}' not found in application '{application}'")

        async with self.session as session:
            await session.execute_write(write)

    async def remove_tag(self, group_id: str, tag_id: str, application: str) -> None:
        async def write(tx: AsyncManagedTransaction) -> None:
            await lock_nodes(tx, "Tag", [tag_id])
            await lock_nodes(tx, "TagGroup", [group_id])
            result = await tx.run(
                TagGroupDatabase.REMOVE_TAG_QUERY, group_id=group_id, tag_id=tag_id, application=application
            )
            if await result.single() is None:
                raise DataNotFoundError(f"Tag '{tag_id}' is not in tag group '{group_id}'")

        async with self.session as session:
            await session.execute_write(write)
