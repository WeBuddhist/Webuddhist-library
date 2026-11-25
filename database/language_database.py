from typing import TYPE_CHECKING, LiteralString

from exceptions import DataValidationError
from models.responses import LanguageResponse

from .repository import SessionRepository, fetch_record, on_constraint_violation, raise_if_blockers

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction


class LanguageDatabase(SessionRepository):
    GET_ALL_QUERY: LiteralString = """
    MATCH (l:Language)
    RETURN l.code AS code, l.name AS name
    ORDER BY l.code
    """

    CREATE_QUERY: LiteralString = """
    CREATE (l:Language {code: $code, name: $name})
    RETURN l.code AS code
    """

    DELETE_CHECK_QUERY: LiteralString = """
    MATCH (l:Language {code: $code})
    RETURN count { ()-[:HAS_LANGUAGE]->(l) } AS reference_count
    """

    DELETE_QUERY: LiteralString = """
    MATCH (l:Language {code: $code})
    DELETE l
    RETURN $code AS code
    """

    async def get_all(self) -> list[LanguageResponse]:
        return await self.query_all(LanguageDatabase.GET_ALL_QUERY, LanguageResponse)

    async def create(self, code: str, name: str) -> str:
        async def write(tx: AsyncManagedTransaction) -> str:
            result = await tx.run(LanguageDatabase.CREATE_QUERY, code=code, name=name)
            record = await result.single(strict=True)
            return str(record["code"])

        with on_constraint_violation(DataValidationError, f"Language with code '{code}' already exists"):
            return await self.execute_write(write)

    async def delete(self, code: str) -> None:
        async def write(tx: AsyncManagedTransaction) -> None:
            not_found = f"Language with code '{code}' not found"
            record = await fetch_record(tx, LanguageDatabase.DELETE_CHECK_QUERY, not_found, code=code)
            raise_if_blockers(
                f"Language '{code}' is referenced by", {"HAS_LANGUAGE relationship(s)": record["reference_count"]}
            )

            await tx.run(LanguageDatabase.DELETE_QUERY, code=code)

        await self.execute_write(write)
