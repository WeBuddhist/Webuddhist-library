from logging import getLogger
from typing import TYPE_CHECKING, Any, LiteralString, Self

from neo4j import AsyncGraphDatabase, AsyncSession, Record, RoutingControl
from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind

from .repository import TransactionWork

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from config import Settings

logger = getLogger(__name__)
tracer = trace.get_tracer("openpecha-api")


class Database:
    """Async Neo4j connection: managed read/write transactions and one-shot queries."""

    def __init__(self, neo4j_uri: str, neo4j_auth: tuple[str, str], neo4j_database: str = "neo4j") -> None:
        self._driver = AsyncGraphDatabase.driver(neo4j_uri, auth=neo4j_auth)
        self._database = neo4j_database

    @classmethod
    def from_settings(cls, config: Settings) -> Self:
        return cls(config.neo4j_uri, (config.neo4j_username, config.neo4j_password), config.neo4j_database)

    async def verify_connectivity(self) -> None:
        await self._driver.verify_connectivity()
        logger.info("Async connection to neo4j established.")

    def get_session(self) -> AsyncSession:
        return self._driver.session(database=self._database)

    def _span(self, operation: str, name: str, **attributes: str) -> AbstractContextManager[Span]:
        return tracer.start_as_current_span(
            f"neo4j {operation} {name}",
            kind=SpanKind.CLIENT,
            attributes={"db.system": "neo4j", "db.name": self._database, "db.operation.name": operation} | attributes,
        )

    async def read[R, **P](self, work: TransactionWork[R, P], /, *args: P.args, **kwargs: P.kwargs) -> R:
        """Run `work` in a managed read transaction, retried on transient errors."""
        with self._span("read", getattr(work, "__qualname__", type(work).__name__)):
            async with self.get_session() as session:
                return await session.execute_read(work, *args, **kwargs)

    async def write[R, **P](self, work: TransactionWork[R, P], /, *args: P.args, **kwargs: P.kwargs) -> R:
        """Run `work` in a managed write transaction, retried on transient errors."""
        with self._span("write", getattr(work, "__qualname__", type(work).__name__)):
            async with self.get_session() as session:
                return await session.execute_write(work, *args, **kwargs)

    async def query(self, query: LiteralString, /, *, write: bool = False, **params: Any) -> list[Record]:  # noqa: ANN401
        """Run one statement in its own managed transaction."""
        routing = RoutingControl.WRITE if write else RoutingControl.READ
        with self._span("query", "write" if write else "read", **{"db.query.text": query[:500]}):
            result = await self._driver.execute_query(query, params, routing_=routing, database_=self._database)
        return result.records

    async def close(self) -> None:
        await self._driver.close()

    async def __aenter__(self) -> Self:
        try:
            await self.verify_connectivity()
        except BaseException:
            await self.close()
            raise
        return self

    async def __aexit__(self, _exc_type: object, _exc_val: object, _exc_tb: object) -> None:
        await self.close()
