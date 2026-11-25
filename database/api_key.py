import hashlib
import secrets
from datetime import UTC, datetime
from typing import TYPE_CHECKING, LiteralString

from errors import DataNotFoundError

from .repository import SessionSource

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction

CREATE_QUERY: LiteralString = """
    OPTIONAL MATCH (app:Application {id: $application_id})
    CREATE (k:ApiKey {
        id: $key_id, name: $name, email: $email, api_key_hash: $api_key_hash,
        is_active: true, created_at: datetime($created_at)
    })
    CALL (app, k) {
        WHEN app IS NOT NULL THEN { CREATE (k)-[:BOUND_TO]->(app) }
    }
    RETURN k.id AS id, app.id AS application_id
"""
VALIDATE_KEY_QUERY: LiteralString = """
    MATCH (k:ApiKey {api_key_hash: $api_key_hash, is_active: true})
    OPTIONAL MATCH (k)-[:BOUND_TO]->(a:Application)
    RETURN k.id AS id, a.id AS bound_application_id
"""
REVOKE_QUERY: LiteralString = """
    MATCH (k:ApiKey {id: $key_id})
    SET k.is_active = false
    RETURN k.id AS id
"""
ROTATE_KEY_QUERY: LiteralString = """
    MATCH (k:ApiKey {id: $key_id})
    SET k.api_key_hash = $api_key_hash, k.is_active = true
    RETURN k.id AS id
"""
LIST_QUERY: LiteralString = """
    MATCH (k:ApiKey)
    OPTIONAL MATCH (k)-[:BOUND_TO]->(a:Application)
    RETURN k.id AS id, k.name AS name, k.email AS email, k.is_active AS is_active,
           k.created_at AS created_at, a.id AS bound_application_id
    ORDER BY k.created_at DESC
"""


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _generate_api_key() -> str:
    """32 URL-safe characters."""
    return secrets.token_urlsafe(24)


async def create(
    db: SessionSource, key_id: str, name: str, email: str, application_id: str | None = None
) -> tuple[str, str]:
    """Return the key ID and the raw key, which is not stored and so cannot be shown again."""
    raw_key = _generate_api_key()
    api_key_hash = _hash_key(raw_key)
    created_at = datetime.now(UTC).isoformat()

    async def write(tx: AsyncManagedTransaction) -> str:
        result = await tx.run(
            CREATE_QUERY,
            key_id=key_id,
            name=name,
            email=email,
            api_key_hash=api_key_hash,
            created_at=created_at,
            application_id=application_id,
        )
        record = await result.single(strict=True)
        if application_id is not None and record["application_id"] is None:
            raise DataNotFoundError(f"Application '{application_id}' not found")
        return record["id"]

    return (await db.write(write), raw_key)


async def validate_key(db: SessionSource, raw_key: str) -> dict | None:
    """The active key's `id` and `bound_application_id`, or None for an unknown or revoked key."""
    records = await db.query(VALIDATE_KEY_QUERY, api_key_hash=_hash_key(raw_key))
    return records[0].data() if records else None


async def revoke(db: SessionSource, key_id: str) -> bool:
    """Return whether the key exists."""
    return bool(await db.query(REVOKE_QUERY, write=True, key_id=key_id))


async def rotate_key(db: SessionSource, key_id: str) -> str | None:
    """Replace the key's value and reactivate it; return the new raw key, or None for an unknown key."""
    raw_key = _generate_api_key()
    found = await db.query(ROTATE_KEY_QUERY, write=True, key_id=key_id, api_key_hash=_hash_key(raw_key))
    return raw_key if found else None


async def list_all(db: SessionSource) -> list[dict]:
    return [record.data() for record in await db.query(LIST_QUERY)]
