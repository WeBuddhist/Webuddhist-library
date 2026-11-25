from typing import TYPE_CHECKING, LiteralString

from neo4j.exceptions import ConstraintError

from exceptions import DataConflictError, DataNotFoundError
from identifier import generate_id
from models.person import PersonBase, PersonOutput

from .locking import lock_nodes
from .nomen_database import NomenDatabase
from .repository import SessionRepository

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction

    from models.person import PersonInput, PersonPatch
    from models.requests import PersonFilter


class PersonDatabase(SessionRepository):
    _PERSON_RETURN: LiteralString = f"""
    {{
        id: p.id,
        bdrc: p.bdrc,
        wiki: p.wiki,
        name: {NomenDatabase.localized_map("(p)-[:HAS_NAME]->(:Nomen)")},
        alt_names: nullIf({NomenDatabase.alternative_maps("(p)-[:HAS_NAME]->(:Nomen)")}, [])
    }}
    """

    GET_QUERY: LiteralString = f"""
    MATCH (p:Person {{id: $id}})
    RETURN {_PERSON_RETURN} AS person
    """

    GET_BY_IDS_QUERY: LiteralString = f"""
    UNWIND range(0, size($ids) - 1) AS idx
    MATCH (p:Person {{id: $ids[idx]}})
    WITH p, idx
    ORDER BY idx
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

    UPDATE_PROPERTIES_QUERY: LiteralString = """
    MATCH (p:Person {id: $id})
    SET p += $properties
    RETURN p.id as person_id
    """

    DELETE_CHECK_QUERY: LiteralString = """
    MATCH (p:Person {id: $person_id})
    RETURN count { (:Contribution)-[:BY]->(p) } AS contribution_count
    """

    DELETE_QUERY: LiteralString = """
    MATCH (p:Person {id: $person_id})
    OPTIONAL MATCH (p)-[:HAS_NAME]->(n:Nomen)
    OPTIONAL MATCH (n)-[:HAS_LOCALIZATION]->(lt:LocalizedText)
    OPTIONAL MATCH (n)<-[:ALTERNATIVE_OF]-(alt:Nomen)-[:HAS_LOCALIZATION]->(alt_lt:LocalizedText)
    DETACH DELETE p, n, lt, alt, alt_lt
    RETURN $person_id AS person_id
    """

    async def get(self, person_id: str) -> PersonOutput:
        return await self.execute_read(self.get_with_transaction, person_id)

    @staticmethod
    async def get_with_transaction(tx: AsyncManagedTransaction, person_id: str) -> PersonOutput:
        result = await tx.run(PersonDatabase.GET_QUERY, id=person_id)
        record = await result.single()
        if record is None:
            raise DataNotFoundError(f"Person with ID '{person_id}' not found")
        return PersonOutput.model_validate(record["person"])

    async def get_by_ids(self, person_ids: list[str]) -> list[PersonOutput]:
        if not person_ids:
            return []

        async def read(tx: AsyncManagedTransaction) -> list[PersonOutput]:
            result = await tx.run(PersonDatabase.GET_BY_IDS_QUERY, ids=person_ids)
            records = await result.data()
            return [PersonOutput.model_validate(record["person"]) for record in records]

        return await self.execute_read(read)

    async def get_all(
        self,
        offset: int = 0,
        limit: int = 20,
        filters: PersonFilter | None = None,
    ) -> list[PersonOutput]:
        async def read(tx: AsyncManagedTransaction) -> list[PersonOutput]:
            result = await tx.run(
                PersonDatabase.GET_ALL_QUERY,
                offset=offset,
                limit=limit,
                bdrc=filters.bdrc if filters else None,
                wiki=filters.wiki if filters else None,
            )
            records = await result.data()
            return [PersonOutput.model_validate(record["person"]) for record in records]

        return await self.execute_read(read)

    async def create(self, person: PersonInput) -> str:
        async def create_transaction(tx: AsyncManagedTransaction) -> str:
            person_id = generate_id()
            alt_names_data = [alt_name.root for alt_name in person.alt_names] if person.alt_names else None
            primary_nomen_id = await NomenDatabase.create_with_transaction(tx, person.name.root, alt_names_data)

            result = await tx.run(
                PersonDatabase.CREATE_QUERY,
                id=person_id,
                bdrc=person.bdrc,
                wiki=person.wiki,
                primary_nomen_id=primary_nomen_id,
            )
            record = await result.single(strict=True)
            return str(record["person_id"])

        async with self.session as session:
            try:
                return str(await session.execute_write(create_transaction))
            except ConstraintError as e:
                raise DataConflictError(str(e)) from e

    async def update(self, person_id: str, patch: PersonPatch) -> PersonOutput:
        async def update_transaction(tx: AsyncManagedTransaction) -> PersonOutput:
            await lock_nodes(tx, "Person", [person_id])
            result = await tx.run(PersonDatabase.GET_QUERY, id=person_id)
            record = await result.single()
            if record is None:
                raise DataNotFoundError(f"Person with ID '{person_id}' not found")

            properties = patch.model_dump(include={"bdrc", "wiki"})
            if properties:
                await tx.run(PersonDatabase.UPDATE_PROPERTIES_QUERY, id=person_id, properties=properties)

            if patch.model_fields_set & {"name", "alt_names"}:
                updated = PersonBase.model_validate(record["person"] | patch.model_dump(), extra="ignore")
                aliases = [name.root for name in updated.alt_names or []]
                await NomenDatabase.replace_with_transaction(
                    tx, "Person", person_id, updated.name.root, relationship="HAS_NAME", alternative_texts=aliases
                )
            return await self.get_with_transaction(tx, person_id)

        async with self.session as session:
            try:
                return await session.execute_write(update_transaction)
            except ConstraintError as e:
                raise DataConflictError(str(e)) from e

    async def delete(self, person_id: str) -> None:
        async def write(tx: AsyncManagedTransaction) -> None:
            result = await tx.run(PersonDatabase.DELETE_CHECK_QUERY, person_id=person_id)
            record = await result.single()
            if not record:
                raise DataNotFoundError(f"Person with ID '{person_id}' not found")
            if record["contribution_count"] > 0:
                raise DataConflictError(
                    f"Person '{person_id}' has {record['contribution_count']} contribution(s) and cannot be deleted"
                )

            await tx.run(PersonDatabase.DELETE_QUERY, person_id=person_id)

        await self.execute_write(write)
