from collections.abc import AsyncIterator, Awaitable, Callable, Generator, Mapping
from contextlib import contextmanager
from functools import wraps
from typing import Any, Concatenate, LiteralString, Protocol, runtime_checkable

from neo4j import AsyncManagedTransaction, Record
from neo4j.exceptions import ConstraintError
from pydantic import BaseModel

from errors import DataConflictError, DataNotFoundError, OpenPechaError

type TransactionWork[R, **P] = Callable[Concatenate[AsyncManagedTransaction, P], Awaitable[R]]


@runtime_checkable
class SessionSource(Protocol):
    async def read[R, **P](self, work: TransactionWork[R, P], /, *args: P.args, **kwargs: P.kwargs) -> R: ...

    async def write[R, **P](self, work: TransactionWork[R, P], /, *args: P.args, **kwargs: P.kwargs) -> R: ...

    async def query(self, query: LiteralString, /, *, write: bool = False, **params: Any) -> list[Record]: ...  # noqa: ANN401


type SessionWork[R, **P] = Callable[Concatenate[SessionSource | AsyncManagedTransaction, P], Awaitable[R]]


def reading[R, **P](fn: TransactionWork[R, P]) -> SessionWork[R, P]:
    """`await fn(db, …)` runs a read transaction; `await fn(tx, …)` uses the open one."""

    @wraps(fn)
    async def wrapper(db_or_tx: SessionSource | AsyncManagedTransaction, /, *args: P.args, **kwargs: P.kwargs) -> R:
        if isinstance(db_or_tx, SessionSource):
            return await db_or_tx.read(fn, *args, **kwargs)
        return await fn(db_or_tx, *args, **kwargs)

    return wrapper


def writing[R, **P](fn: TransactionWork[R, P]) -> SessionWork[R, P]:
    """`await fn(db, …)` runs a write transaction; `await fn(tx, …)` uses the open one."""

    @wraps(fn)
    async def wrapper(db_or_tx: SessionSource | AsyncManagedTransaction, /, *args: P.args, **kwargs: P.kwargs) -> R:
        if isinstance(db_or_tx, SessionSource):
            return await db_or_tx.write(fn, *args, **kwargs)
        return await fn(db_or_tx, *args, **kwargs)

    return wrapper


def _validate[M: BaseModel](model: type[M], record: Mapping[str, Any], field: str | None) -> M:
    return model.model_validate(record if field is None else record[field])


async def create_id(tx: AsyncManagedTransaction, query: LiteralString, field: str, /, **params: Any) -> str:  # noqa: ANN401
    """Return the persisted identifier, requiring exactly one result before commit."""
    result = await tx.run(query, **params)
    record = await result.single(strict=True)
    return str(record[field])


async def fetch_record(tx: AsyncManagedTransaction, query: LiteralString, not_found: str, /, **params: Any) -> Record:  # noqa: ANN401
    record = await (await tx.run(query, **params)).single()
    if record is None:
        raise DataNotFoundError(not_found)
    return record


async def fetch_one[M: BaseModel](
    tx: AsyncManagedTransaction,
    query: LiteralString,
    model: type[M],
    not_found: str,
    /,
    *,
    field: str | None = None,
    **params: Any,  # noqa: ANN401
) -> M:
    return _validate(model, await fetch_record(tx, query, not_found, **params), field)


async def fetch_all[M: BaseModel](
    tx: AsyncManagedTransaction,
    query: LiteralString,
    model: type[M],
    /,
    *,
    field: str | None = None,
    **params: Any,  # noqa: ANN401
) -> list[M]:
    return [_validate(model, record, field) for record in await (await tx.run(query, **params)).data()]


async def require(tx: AsyncManagedTransaction, pattern: LiteralString, error: OpenPechaError, /, **params: Any) -> None:  # noqa: ANN401
    result = await tx.run(f"RETURN EXISTS {{ {pattern} }} AS exists", **params)
    if not (await result.single(strict=True))["exists"]:
        raise error


async def require_node(tx: AsyncManagedTransaction, label: LiteralString, node_id: str, error: OpenPechaError) -> None:
    await require(tx, f"(:{label} {{id: $id}})", error, id=node_id)


async def set_properties(
    tx: AsyncManagedTransaction, label: LiteralString, node_id: str, properties: Mapping[str, Any]
) -> None:
    if properties:
        await tx.run(f"MATCH (n:{label} {{id: $id}}) SET n += $properties FINISH", id=node_id, properties=properties)


def raise_if_blockers(prefix: str, counts: Mapping[str, int]) -> None:
    """Reject a delete with e.g. "<prefix> 2 edition(s), 1 audio(s)" for every nonzero count."""
    blockers = [f"{count} {noun}" for noun, count in counts.items() if count]
    if blockers:
        raise DataConflictError(f"{prefix} {', '.join(blockers)}")


@contextmanager
def on_constraint_violation(
    error: type[OpenPechaError] = DataConflictError, message: str | None = None
) -> Generator[None]:
    try:
        yield
    except ConstraintError as exc:
        raise error(message or str(exc)) from exc


async def query_record(db: SessionSource, query: LiteralString, not_found: str, /, **params: Any) -> Record:  # noqa: ANN401
    records = await db.query(query, **params)
    if not records:
        raise DataNotFoundError(not_found)
    return records[0]


async def query_one[M: BaseModel](
    db: SessionSource,
    query: LiteralString,
    model: type[M],
    not_found: str,
    /,
    *,
    field: str | None = None,
    **params: Any,  # noqa: ANN401
) -> M:
    return _validate(model, await query_record(db, query, not_found, **params), field)


async def query_all[M: BaseModel](
    db: SessionSource,
    query: LiteralString,
    model: type[M],
    /,
    *,
    field: str | None = None,
    **params: Any,  # noqa: ANN401
) -> list[M]:
    return [_validate(model, record, field) for record in await db.query(query, **params)]


async def iter_id_batches(db: SessionSource, query: LiteralString) -> AsyncIterator[list[Record]]:
    """The query selects IDs greater than $after and returns one row per ID, aliased as id."""
    after = ""
    while rows := await db.query(query + "\nORDER BY id LIMIT 100", after=after):
        yield rows
        after = rows[-1]["id"]
