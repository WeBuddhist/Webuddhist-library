from typing import TYPE_CHECKING, LiteralString

from pydantic import MISSING

from database.repository import create_id, writing
from errors import DataNotFoundError, DataValidationError, ForbiddenError
from identifier import generate_id
from models.category import CategoryOutput

from . import nomen
from .locking import lock_nodes
from .repository import SessionSource, fetch_one, fetch_record, query_all, raise_if_blockers, reading, require
from .tag import find_existing_title_query, validate_unique_title

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


_CATEGORY_RETURN: LiteralString = f"""
{{
    id: c.id,
    title: {nomen.localized_map("(c)-[:HAS_TITLE]->(:Nomen)")},
    description: {nomen.optional_localized_map("(c)-[:HAS_DESCRIPTION]->(:Nomen)")},
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
RETURN root.id AS id, count(DISTINCT w) AS work_count
"""
DELETE_QUERY: LiteralString = f"""
MATCH (root:Category {{id: $category_id}})-[:BELONGS_TO]->(app:Application {{id: $application}})
MATCH (c:Category)-[:HAS_PARENT*0..]->(root)
WHERE (c)-[:BELONGS_TO]->(app)
WITH collect(DISTINCT c) AS categories
WITH categories, size(categories) AS deleted_count
UNWIND categories AS c
{nomen.delete_names("c", "HAS_TITLE", "HAS_DESCRIPTION")}
DETACH DELETE c
WITH DISTINCT deleted_count
RETURN deleted_count
"""


@reading
async def get(tx: AsyncManagedTransaction, category_id: str, application: str) -> CategoryOutput:
    return await fetch_one(
        tx,
        GET_BY_ID_QUERY,
        CategoryOutput,
        f"Category '{category_id}' not found in application '{application}'",
        field="category",
        category_id=category_id,
        application=application,
    )


async def _validate_parent_exists(tx: AsyncManagedTransaction, application: str, parent_id: str) -> None:
    await require(
        tx,
        "(:Category {id: $parent_id})-[:BELONGS_TO]->(:Application {id: $application})",
        DataNotFoundError(f"Parent category '{parent_id}' not found in application '{application}'"),
        application=application,
        parent_id=parent_id,
    )


async def _validate_not_exists(
    tx: AsyncManagedTransaction,
    application: str,
    title: dict[str, str],
    parent_id: str | None,
    category_id: str | None = None,
) -> None:
    await validate_unique_title(
        tx, "Category", FIND_EXISTING_QUERY, application, title, parent_id=parent_id, category_id=category_id
    )


async def validate_category_exists(
    tx: AsyncManagedTransaction, category_id: str, application: str | None = None
) -> None:
    """Validate that a category with the given ID exists.

    Raises DataValidationError if the category does not exist.
    """
    query = """
    MATCH (c:Category {id: $category_id})-[:BELONGS_TO]->(app:Application)
    RETURN app.id AS application
    """
    result = await tx.run(query, category_id=category_id)
    record = await result.single()
    if not record:
        raise DataValidationError(
            f"Category with ID '{category_id}' does not exist. Please provide a valid category_id."
        )
    if application is not None and record["application"] != application:
        raise ForbiddenError("Category belongs to another application")


async def get_all(db: SessionSource, application: str, parent_id: str | None = None) -> list[CategoryOutput]:
    query = GET_CHILDREN_QUERY if parent_id else GET_ROOTS_QUERY
    return await query_all(db, query, CategoryOutput, field="category", application=application, parent_id=parent_id)


@writing
async def create(tx: AsyncManagedTransaction, category: CategoryInput, application: str) -> str:
    if category.parent_id is not None:
        await _validate_parent_exists(tx, application, category.parent_id)
    await _validate_not_exists(tx, application, category.title.root, category.parent_id)
    category_id = generate_id()
    nomen_id = await nomen.create(tx, category.title.root)
    description = category.description.root if category.description else None
    description_nomen_id = await nomen.create_optional(tx, description)
    return await create_id(
        tx,
        CREATE_QUERY,
        "category_id",
        category_id=category_id,
        application=application,
        nomen_id=nomen_id,
        parent_id=category.parent_id,
        description_nomen_id=description_nomen_id,
    )


@writing
async def update(
    tx: AsyncManagedTransaction, category_id: str, patch: CategoryPatch, application: str
) -> CategoryOutput:
    await lock_nodes(tx, "Category", [category_id])
    existing = await get(tx, category_id, application)
    if patch.title is not MISSING:
        await _validate_not_exists(tx, application, patch.title.root, existing.parent_id, category_id=category_id)
        title = _merge_localizations(existing.title.root, patch.title.root)
        await nomen.replace(tx, "Category", category_id, title)
    if patch.description is not MISSING:
        existing_description = existing.description.root if existing.description else {}
        description = _merge_localizations(existing_description, patch.description.root)
        await nomen.replace(tx, "Category", category_id, description, relationship="HAS_DESCRIPTION")
    return await get(tx, category_id, application)


@writing
async def delete(tx: AsyncManagedTransaction, category_id: str, application: str) -> None:
    params = {"category_id": category_id, "application": application}
    not_found = f"Category '{category_id}' not found in application '{application}'"
    check = await fetch_record(tx, DELETE_CHECK_QUERY, not_found, **params)
    raise_if_blockers(
        f"Category '{category_id}' cannot be deleted because it or its subcategories are referenced by",
        {"work(s)": check["work_count"]},
    )
    await tx.run(DELETE_QUERY, params)
