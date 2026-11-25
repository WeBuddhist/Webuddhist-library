from typing import TYPE_CHECKING, LiteralString

from identifier import generate_id
from models.person import PersonInput, PersonOutput

from . import nomen
from .cypher import match_in_id_order
from .locking import lock_nodes
from .repository import (
    SessionSource,
    create_id,
    fetch_one,
    fetch_record,
    on_constraint_violation,
    query_all,
    raise_if_blockers,
    reading,
    set_properties,
    writing,
)

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction

    from models.person import PersonPatch
    from models.requests import PersonFilter

_PERSON_RETURN: LiteralString = f"""
{{
    id: p.id,
    bdrc: p.bdrc,
    wiki: p.wiki,
    name: {nomen.localized_map("(p)-[:HAS_NAME]->(:Nomen)")},
    alt_names: nullIf({nomen.alternative_maps("(p)-[:HAS_NAME]->(:Nomen)")}, [])
}}
"""
GET_QUERY: LiteralString = f"""
MATCH (p:Person {{id: $id}})
RETURN {_PERSON_RETURN} AS person
"""
GET_BY_IDS_QUERY: LiteralString = f"""
{match_in_id_order("p", "Person")}
RETURN {_PERSON_RETURN} AS person
"""
GET_ALL_QUERY: LiteralString = f"""
CALL () {{
    WHEN $bdrc IS NOT NULL THEN {{ MATCH (p:Person {{bdrc: $bdrc}}) RETURN p }}
    WHEN $wiki IS NOT NULL THEN {{ MATCH (p:Person {{wiki: $wiki}}) RETURN p }}
    ELSE {{ MATCH (p:Person) RETURN p }}
}}
WITH p
WHERE $wiki IS NULL OR p.wiki = $wiki
ORDER BY p.id SKIP $offset LIMIT $limit
RETURN {_PERSON_RETURN} AS person
"""
CREATE_QUERY: LiteralString = """
MATCH (n:Nomen {id: $primary_nomen_id})
CREATE (p:Person {id: $id, bdrc: $bdrc, wiki: $wiki})
CREATE (p)-[:HAS_NAME]->(n)
RETURN p.id as person_id
"""
DELETE_CHECK_QUERY: LiteralString = """
MATCH (p:Person {id: $person_id})
RETURN count { (:Contribution)-[:BY]->(p) } AS contribution_count
"""
DELETE_QUERY: LiteralString = f"""
MATCH (p:Person {{id: $person_id}})
{nomen.delete_names("p", "HAS_NAME")}
DETACH DELETE p
RETURN $person_id AS person_id
"""


@reading
async def get(tx: AsyncManagedTransaction, person_id: str) -> PersonOutput:
    return await fetch_one(
        tx, GET_QUERY, PersonOutput, f"Person with ID '{person_id}' not found", field="person", id=person_id
    )


async def get_by_ids(db: SessionSource, person_ids: list[str]) -> list[PersonOutput]:
    if not person_ids:
        return []
    return await query_all(db, GET_BY_IDS_QUERY, PersonOutput, field="person", ids=person_ids)


async def get_all(
    db: SessionSource, offset: int = 0, limit: int = 20, filters: PersonFilter | None = None
) -> list[PersonOutput]:
    return await query_all(
        db,
        GET_ALL_QUERY,
        PersonOutput,
        field="person",
        offset=offset,
        limit=limit,
        bdrc=filters.bdrc if filters else None,
        wiki=filters.wiki if filters else None,
    )


async def create(db: SessionSource, person: PersonInput) -> str:

    async def create_transaction(tx: AsyncManagedTransaction) -> str:
        person_id = generate_id()
        alt_names_data = [name.root for name in person.alt_names or []]
        primary_nomen_id = await nomen.create(tx, person.name.root, alt_names_data)
        return await create_id(
            tx,
            CREATE_QUERY,
            "person_id",
            id=person_id,
            bdrc=person.bdrc,
            wiki=person.wiki,
            primary_nomen_id=primary_nomen_id,
        )

    with on_constraint_violation():
        return await db.write(create_transaction)


async def update(db: SessionSource, person_id: str, patch: PersonPatch) -> PersonOutput:

    async def update_transaction(tx: AsyncManagedTransaction) -> PersonOutput:
        await lock_nodes(tx, "Person", [person_id])
        not_found = f"Person with ID '{person_id}' not found"
        record = await fetch_record(tx, GET_QUERY, not_found, id=person_id)
        await set_properties(tx, "Person", person_id, patch.model_dump(include={"bdrc", "wiki"}))
        if patch.model_fields_set & {"name", "alt_names"}:
            updated = PersonInput.model_validate(record["person"] | patch.model_dump(), extra="ignore")
            aliases = [name.root for name in updated.alt_names or []]
            await nomen.replace(
                tx, "Person", person_id, updated.name.root, relationship="HAS_NAME", alternative_texts=aliases
            )
        return await get(tx, person_id)

    with on_constraint_violation():
        return await db.write(update_transaction)


@writing
async def delete(tx: AsyncManagedTransaction, person_id: str) -> None:
    not_found = f"Person with ID '{person_id}' not found"
    record = await fetch_record(tx, DELETE_CHECK_QUERY, not_found, person_id=person_id)
    raise_if_blockers(
        f"Person '{person_id}' cannot be deleted because it has", {"contribution(s)": record["contribution_count"]}
    )
    await tx.run(DELETE_QUERY, person_id=person_id)
