from typing import TYPE_CHECKING, LiteralString

from .repository import SessionRepository, fetch_record, on_constraint_violation, raise_if_blockers

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction


class ApplicationDatabase(SessionRepository):
    EXISTS_QUERY: LiteralString = "RETURN EXISTS { (:Application {id: $application_id}) } AS exists"
    CREATE_QUERY: LiteralString = "CREATE (a:Application {id: $application_id, name: $name}) RETURN a.id AS id"
    DELETE_CHECK_QUERY: LiteralString = """
    MATCH (a:Application {id: $application_id})
    RETURN count { (:Tag)-[:BELONGS_TO]->(a) } AS tag_count,
           count { (:Category)-[:BELONGS_TO]->(a) } AS category_count,
           count { (:ApiKey)-[:BOUND_TO]->(a) } AS api_key_count
    """
    DELETE_QUERY: LiteralString = """
    MATCH (a:Application {id: $application_id})
    WITH a, a.id AS id
    DELETE a
    RETURN id
    """

    async def exists(self, application_id: str) -> bool:
        [record] = await self.query(self.EXISTS_QUERY, application_id=application_id)
        return record["exists"]

    async def create(self, application_id: str, name: str) -> str:
        async def write(tx: AsyncManagedTransaction) -> str:
            result = await tx.run(self.CREATE_QUERY, application_id=application_id, name=name)
            record = await result.single(strict=True)
            return str(record["id"])

        with on_constraint_violation():
            return await self.execute_write(write)

    async def delete(self, application_id: str) -> None:
        async def write(tx: AsyncManagedTransaction) -> None:
            not_found = f"Application '{application_id}' not found"
            record = await fetch_record(tx, self.DELETE_CHECK_QUERY, not_found, application_id=application_id)
            raise_if_blockers(
                f"Application '{application_id}' is still referenced by",
                {
                    "tag(s)": record["tag_count"],
                    "category/categories": record["category_count"],
                    "API key(s)": record["api_key_count"],
                },
            )
            await tx.run(self.DELETE_QUERY, application_id=application_id)

        await self.execute_write(write)
