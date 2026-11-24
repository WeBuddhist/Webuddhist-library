from collections.abc import Awaitable, Callable
from typing import Concatenate, Protocol, runtime_checkable

from neo4j import AsyncManagedTransaction, AsyncSession


@runtime_checkable
class _SessionProvider(Protocol):
    def get_session(self) -> AsyncSession: ...


type SessionSource = Callable[[], AsyncSession] | _SessionProvider
type TransactionWork[R, **P] = Callable[Concatenate[AsyncManagedTransaction, P], Awaitable[R]]


class SessionRepository:
    def __init__(self, db: SessionSource) -> None:
        self._get_session = db.get_session if isinstance(db, _SessionProvider) else db

    @property
    def session(self) -> AsyncSession:
        """Open a fresh session for the calling operation."""
        return self._get_session()

    async def execute_read[R, **P](self, work: TransactionWork[R, P], /, *args: P.args, **kwargs: P.kwargs) -> R:
        async with self.session as session:
            return await session.execute_read(work, *args, **kwargs)

    async def execute_write[R, **P](self, work: TransactionWork[R, P], /, *args: P.args, **kwargs: P.kwargs) -> R:
        async with self.session as session:
            return await session.execute_write(work, *args, **kwargs)
