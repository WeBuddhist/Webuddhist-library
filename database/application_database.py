from typing import TYPE_CHECKING, LiteralString

from neo4j.exceptions import ConstraintError

from exceptions import DataConflictError, DataNotFoundError

from .repository import SessionRepository

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
        async def read(tx: AsyncManagedTransaction) -> bool:
            result = await tx.run(self.EXISTS_QUERY, application_id=application_id)
            record = await result.single()
            return bool(record and record["exists"])

        return await self.execute_read(read)

    async def create(self, application_id: str, name: str) -> str:
        async def write(tx: AsyncManagedTransaction) -> str:
            result = await tx.run(self.CREATE_QUERY, application_id=application_id, name=name)
            record = await result.single(strict=True)
            return str(record["id"])

        try:
            return await self.execute_write(write)
        except ConstraintError as e:
            raise DataConflictError(str(e)) from e

    async def delete(self, application_id: str) -> None:
        async def write(tx: AsyncManagedTransaction) -> None:
            result = await tx.run(self.DELETE_CHECK_QUERY, application_id=application_id)
            record = await result.single()
            if record is None:
                raise DataNotFoundError(f"Application '{application_id}' not found")

            blockers = []
            if record["tag_count"]:
                blockers.append(f"{record['tag_count']} tag(s)")
            if record["category_count"]:
                blockers.append(f"{record['category_count']} category/categories")
            if record["api_key_count"]:
                blockers.append(f"{record['api_key_count']} API key(s)")
            if blockers:
                raise DataConflictError(f"Application '{application_id}' is still referenced by {', '.join(blockers)}")

            await tx.run(self.DELETE_QUERY, application_id=application_id)

        await self.execute_write(write)
