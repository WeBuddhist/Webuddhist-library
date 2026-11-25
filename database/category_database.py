from typing import TYPE_CHECKING, LiteralString

from pydantic import MISSING

from exceptions import DataNotFoundError
from identifier import generate_id
from models.category import CategoryOutput

from .locking import lock_nodes
from .nomen_database import NomenDatabase
from .repository import SessionRepository, fetch_one, fetch_record, raise_if_blockers, require
from .tag_database import find_existing_title_query, validate_unique_title

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction

    from models.category import CategoryInput, CategoryPatch


def _merge_localizations(existing: dict[str, str], patch: dict[str, str]) -> dict[str, str]:
    """Add or replace the patch's languages, matching language codes case-insensitively.

    The patch's spelling of a language code replaces the existing one, so patching
    ``en-us`` over ``en-US`` leaves a single ``en-us`` entry.
    """
    replaced = {language.lower() for language in patch}
    kept = {language: text for language, text in existing.items() if language.lower() not in replaced}
    return kept | patch


class CategoryDatabase(SessionRepository):
    _CATEGORY_RETURN: LiteralString = f"""
    {{
        id: c.id,
        title: {NomenDatabase.localized_map("(c)-[:HAS_TITLE]->(:Nomen)")},
        description: {NomenDatabase.optional_localized_map("(c)-[:HAS_DESCRIPTION]->(:Nomen)")},
        parent_id: [(c)-[:HAS_PARENT]->(parent:Category) | parent.id][0],
        children: [(child:Category)-[:HAS_PARENT]->(c) | child.id]
    }} AS category
    """

    GET_ROOTS_QUERY: LiteralString = f"""
    MATCH (c:Category)-[:BELONGS_TO]->(:Application {{id: $application}})
    WHERE NOT EXISTS {{ (c)-[:HAS_PARENT]->(:Category) }}
    RETURN {_CATEGORY_RETURN}
    """

    GET_CHILDREN_QUERY: LiteralString = f"""
    MATCH (c:Category)-[:HAS_PARENT]->(:Category {{id: $parent_id}})
    WHERE (c)-[:BELONGS_TO]->(:Application {{id: $application}})
    RETURN {_CATEGORY_RETURN}
    """

    CREATE_QUERY: LiteralString = """
        MATCH (n:Nomen {id: $nomen_id})
        MATCH (app:Application {id: $application})
        CREATE (c:Category {id: $category_id})
        CREATE (c)-[:HAS_TITLE]->(n)
        CREATE (c)-[:BELONGS_TO]->(app)
        WITH c, app
        OPTIONAL MATCH (parent:Category {id: $parent_id})-[:BELONGS_TO]->(app)
        OPTIONAL MATCH (desc_nomen:Nomen {id: $description_nomen_id})
        WITH c, parent, desc_nomen
        CALL (*) { WHEN parent IS NOT NULL THEN { CREATE (c)-[:HAS_PARENT]->(parent) } }
        CALL (*) { WHEN desc_nomen IS NOT NULL THEN { CREATE (c)-[:HAS_DESCRIPTION]->(desc_nomen) } }
        RETURN c.id AS category_id
    """

    GET_BY_ID_QUERY: LiteralString = f"""
    MATCH (c:Category {{id: $category_id}})-[:BELONGS_TO]->(:Application {{id: $application}})
    RETURN {_CATEGORY_RETURN}
    """

    FIND_EXISTING_QUERY: LiteralString = find_existing_title_query(
        "Category",
        """
        WHERE (($parent_id IS NULL AND NOT EXISTS { (n)-[:HAS_PARENT]->(:Category) })
           OR (n)-[:HAS_PARENT]->(:Category {id: $parent_id}))
          AND ($category_id IS NULL OR n.id <> $category_id)
        """,
    )

    DELETE_CHECK_QUERY: LiteralString = """
    MATCH (root:Category {id: $category_id})-[:BELONGS_TO]->(app:Application {id: $application})
    OPTIONAL MATCH (c:Category)-[:HAS_PARENT*0..]->(root)
    WHERE (c)-[:BELONGS_TO]->(app)
    OPTIONAL MATCH (w:Work)-[:HAS_CATEGORY]->(c)
    RETURN count(DISTINCT w) AS work_count
    """

    DELETE_QUERY: LiteralString = """
    MATCH (root:Category {id: $category_id})-[:BELONGS_TO]->(app:Application {id: $application})
    MATCH (c:Category)-[:HAS_PARENT*0..]->(root)
    WHERE (c)-[:BELONGS_TO]->(app)
    WITH collect(DISTINCT c) AS categories
    WITH categories, size(categories) AS deleted_count
    UNWIND categories AS c
    OPTIONAL MATCH (c)-[:HAS_TITLE]->(title_nomen:Nomen)
    OPTIONAL MATCH (title_nomen)-[:HAS_LOCALIZATION]->(title_lt:LocalizedText)
    OPTIONAL MATCH (c)-[:HAS_DESCRIPTION]->(desc_nomen:Nomen)
    OPTIONAL MATCH (desc_nomen)-[:HAS_LOCALIZATION]->(desc_lt:LocalizedText)
    DETACH DELETE c, title_nomen, title_lt, desc_nomen, desc_lt
    WITH DISTINCT deleted_count
    RETURN deleted_count
    """

    async def get_by_id(self, category_id: str, application: str) -> CategoryOutput:
        return await self.execute_read(self.get_with_transaction, category_id, application)

    @staticmethod
    async def get_with_transaction(tx: AsyncManagedTransaction, category_id: str, application: str) -> CategoryOutput:
        return await fetch_one(
            tx,
            CategoryDatabase.GET_BY_ID_QUERY,
            CategoryOutput,
            f"Category '{category_id}' not found in application '{application}'",
            field="category",
            category_id=category_id,
            application=application,
        )

    async def get_all(self, application: str, parent_id: str | None = None) -> list[CategoryOutput]:
        query = CategoryDatabase.GET_CHILDREN_QUERY if parent_id else CategoryDatabase.GET_ROOTS_QUERY
        return await self.query_all(
            query, CategoryOutput, field="category", application=application, parent_id=parent_id
        )

    async def create(self, category: CategoryInput, application: str) -> str:
        async def create_transaction(tx: AsyncManagedTransaction) -> str:
            if category.parent_id is not None:
                await self._validate_parent_exists_tx(tx, application, category.parent_id)
            await self._validate_not_exists_tx(tx, application, category.title.root, category.parent_id)

            category_id = generate_id()
            nomen_id = await NomenDatabase.create_with_transaction(tx, category.title.root, None)
            description = category.description.root if category.description else None
            description_nomen_id = await NomenDatabase.create_optional_with_transaction(tx, description)

            result = await tx.run(
                CategoryDatabase.CREATE_QUERY,
                category_id=category_id,
                application=application,
                nomen_id=nomen_id,
                parent_id=category.parent_id,
                description_nomen_id=description_nomen_id,
            )
            record = await result.single(strict=True)
            return record["category_id"]

        return str(await self.execute_write(create_transaction))

    async def update(self, category_id: str, patch: CategoryPatch, application: str) -> CategoryOutput:
        async def update_transaction(tx: AsyncManagedTransaction) -> CategoryOutput:
            await lock_nodes(tx, "Category", [category_id])
            existing = await self.get_with_transaction(tx, category_id, application)

            if patch.title is not MISSING:
                await self._validate_not_exists_tx(
                    tx, application, patch.title.root, existing.parent_id, category_id=category_id
                )
                title = _merge_localizations(existing.title.root, patch.title.root)
                await NomenDatabase.replace_with_transaction(tx, "Category", category_id, title)

            if patch.description is not MISSING:
                existing_description = existing.description.root if existing.description else {}
                description = _merge_localizations(existing_description, patch.description.root)
                await NomenDatabase.replace_with_transaction(
                    tx, "Category", category_id, description, relationship="HAS_DESCRIPTION"
                )

            return await self.get_with_transaction(tx, category_id, application)

        return await self.execute_write(update_transaction)

    async def _validate_parent_exists_tx(self, tx: AsyncManagedTransaction, application: str, parent_id: str) -> None:
        await require(
            tx,
            "(:Category {id: $parent_id})-[:BELONGS_TO]->(:Application {id: $application})",
            DataNotFoundError(f"Parent category '{parent_id}' not found in application '{application}'"),
            application=application,
            parent_id=parent_id,
        )

    async def _validate_not_exists_tx(
        self,
        tx: AsyncManagedTransaction,
        application: str,
        title: dict[str, str],
        parent_id: str | None,
        category_id: str | None = None,
    ) -> None:
        await validate_unique_title(
            tx,
            "Category",
            CategoryDatabase.FIND_EXISTING_QUERY,
            application,
            title,
            parent_id=parent_id,
            category_id=category_id,
        )

    async def delete(self, category_id: str, application: str) -> None:
        async def write(tx: AsyncManagedTransaction) -> None:
            params = {"category_id": category_id, "application": application}
            not_found = f"Category '{category_id}' not found in application '{application}'"
            check = await fetch_record(tx, CategoryDatabase.DELETE_CHECK_QUERY, not_found, **params)
            raise_if_blockers(
                f"Category '{category_id}' cannot be deleted because it or its subcategories are referenced by",
                {"work(s)": check["work_count"]},
            )
            await tx.run(CategoryDatabase.DELETE_QUERY, params)

        await self.execute_write(write)
