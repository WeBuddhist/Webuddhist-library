import hashlib
import secrets
from datetime import UTC, datetime
from typing import TYPE_CHECKING, LiteralString

from .repository import SessionRepository

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction

    from .database import Database


class ApiKeyDatabase(SessionRepository):
    CREATE_QUERY: LiteralString = """
        CREATE (k:ApiKey {
            id: $key_id,
            name: $name,
            email: $email,
            api_key_hash: $api_key_hash,
            is_active: true,
            created_at: datetime($created_at)
        })
        CALL (*) {
            WHEN $application_id IS NOT NULL THEN {
                MATCH (a:Application {id: $application_id})
                CREATE (k)-[:BOUND_TO]->(a)
            }
        }
        RETURN k.id AS id
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

    _db: Database

    @staticmethod
    def _hash_key(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode()).hexdigest()

    @staticmethod
    def _generate_api_key() -> str:
        """32 URL-safe characters."""
        return secrets.token_urlsafe(24)

    async def create(self, key_id: str, name: str, email: str, application_id: str | None = None) -> tuple[str, str]:
        """Return the key ID and the raw key, which is not stored and so cannot be shown again."""
        if application_id is not None and not await self._db.application.exists(application_id):
            raise ValueError(f"Application '{application_id}' not found")

        raw_key = self._generate_api_key()
        api_key_hash = self._hash_key(raw_key)
        created_at = datetime.now(UTC).isoformat()

        async def write(tx: AsyncManagedTransaction) -> str:
            result = await tx.run(
                self.CREATE_QUERY,
                key_id=key_id,
                name=name,
                email=email,
                api_key_hash=api_key_hash,
                created_at=created_at,
                application_id=application_id,
            )
            return (await result.single(strict=True))["id"]

        return await self.execute_write(write), raw_key

    async def validate_key(self, raw_key: str) -> dict | None:
        """The active key's `id` and `bound_application_id`, or None for an unknown or revoked key."""
        records = await self.query(self.VALIDATE_KEY_QUERY, api_key_hash=self._hash_key(raw_key))
        return records[0].data() if records else None

    async def revoke(self, key_id: str) -> bool:
        """Return whether the key exists."""
        return bool(await self.query(self.REVOKE_QUERY, write=True, key_id=key_id))

    async def rotate_key(self, key_id: str) -> str | None:
        """Replace the key's value and reactivate it; return the new raw key, or None for an unknown key."""
        raw_key = self._generate_api_key()
        found = await self.query(self.ROTATE_KEY_QUERY, write=True, key_id=key_id, api_key_hash=self._hash_key(raw_key))
        return raw_key if found else None

    async def list_all(self) -> list[dict]:
        return [record.data() for record in await self.query(self.LIST_QUERY)]
