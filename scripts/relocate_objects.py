"""Relocate active files and empty objects/ in the configured bucket. The default is a read-only preview."""

import argparse
import asyncio
import logging
from hashlib import sha256
from typing import LiteralString

from botocore.exceptions import ClientError
from neo4j import AsyncManagedTransaction

from config import settings
from database import Database
from database.locking import lock_nodes
from database.repository import iter_id_batches
from errors import DataConflictError, DataValidationError
from models.enums import AudioFormat
from storage import Storage, edition_key, recording_key

logger = logging.getLogger(__name__)

# Node label, pointer property and the data needed to derive the destination.
_SOURCES: tuple[tuple[LiteralString, LiteralString, LiteralString], ...] = (
    ("Edition", "content_key", "[(n)-[:EDITION_OF]->(t:Text) | t.id][0]"),
    ("Recording", "audio_key", "n.format"),
)


class RelocationStorage(Storage):
    async def copy_verified(self, source: str, destination: str) -> None:
        """Resume a relocation only if the destination contains an identical file."""
        try:
            await self._client.copy_object(
                Bucket=self.bucket_name,
                Key=destination,
                CopySource={"Bucket": self.bucket_name, "Key": source},
                IfNoneMatch="*",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in {"412", "PreconditionFailed"}:
                raise
        if await self._fingerprint(source) != await self._fingerprint(destination):
            raise DataConflictError(f"Copy verification failed for {source!r} and {destination!r}")

    async def _fingerprint(self, key: str) -> tuple[str, bytes]:
        response = await self._client.get_object(Bucket=self.bucket_name, Key=key)
        digest = sha256()
        async with response["Body"] as body:
            async for chunk in body.iter_chunks(1024 * 1024):
                digest.update(chunk)
        return response["ContentType"], digest.digest()

    async def legacy_objects(self, *, delete: bool) -> int:
        """Count current keys under the retired prefix, optionally removing them in S3-sized batches."""
        location = {"Bucket": self.bucket_name, "Prefix": "objects/"}
        pages = self._client.get_paginator("list_objects_v2").paginate(**location)
        count = 0
        async for page in pages:
            keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            count += len(keys)
            if delete and keys:
                result = await self._client.delete_objects(
                    Bucket=self.bucket_name, Delete={"Objects": keys, "Quiet": True}
                )
                if errors := result.get("Errors"):
                    raise DataConflictError(f"Could not delete legacy objects: {errors!r}")
        if delete and (await self._client.list_objects_v2(**location, MaxKeys=1))["KeyCount"]:
            raise DataConflictError("Files remain under objects/. Check that writers are paused and rerun.")
        return count


async def _switch_key(
    tx: AsyncManagedTransaction,
    label: LiteralString,
    prop: LiteralString,
    node_id: str,
    *,
    source: str,
    destination: str,
) -> None:
    await lock_nodes(tx, label, [node_id])
    result = await tx.run(f"MATCH (n:{label} {{id: $id}}) RETURN n.{prop} AS key", id=node_id)
    record = await result.single()
    if record and record["key"] == destination:
        return  # The previous attempt committed before the driver lost its response.
    if not record or record["key"] != source:
        raise DataConflictError(f"{label} {node_id} no longer points to {source!r}. Rerun the migration.")
    revision = ", n.revision = n.revision + 1" if label == "Edition" else ""
    await tx.run(f"MATCH (n:{label} {{id: $id}}) SET n.{prop} = $key{revision}", id=node_id, key=destination)


async def relocate_objects(db: Database, storage: RelocationStorage, *, apply: bool = False) -> tuple[int, int]:
    """Clear the retired prefix only after every active pointer has switched."""
    count = 0
    for label, prop, detail in _SOURCES:
        query = f"""
            MATCH (n:{label}) WHERE n.id > $after AND n.{prop} STARTS WITH 'objects/'
            RETURN n.id AS id, n.{prop} AS source, {detail} AS detail
        """
        async for rows in iter_id_batches(db, query):
            for row in rows:
                node_id, source = row["id"], row["source"]
                if label == "Edition":
                    if not row["detail"]:
                        raise DataValidationError(f"Edition {node_id} has no parent text")
                    destination = edition_key(row["detail"], node_id, source.removeprefix("objects/"))
                else:
                    destination = recording_key(node_id, AudioFormat(row["detail"]))
                logger.info("%s %s: %s -> %s", label, node_id, source, destination)
                if apply:
                    await storage.copy_verified(source, destination)
                    await db.write(_switch_key, label, prop, node_id, source=source, destination=destination)
                count += 1
    async with db.get_session() as session:
        for label, prop, _ in _SOURCES:
            remaining = await session.run(
                f"""MATCH (n:{label}) WHERE n.{prop} STARTS WITH 'objects/'
                    AND ($apply OR coalesce(n.id, '') = '') RETURN n.id AS id, n.{prop} AS source LIMIT 1""",
                apply=apply,
            )
            if (row := await remaining.single()) is not None:
                if not row["id"]:
                    raise DataValidationError(f"{label} pointing to {row['source']!r} has no ID")
                raise DataConflictError(f"A {label} still references objects/. Pause writers and rerun.")
    return count, await storage.legacy_objects(delete=apply)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="With API/import writers paused, relocate files and delete all current keys under objects/",
    )
    args = parser.parse_args()
    async with (
        Database.from_settings(settings) as db,
        RelocationStorage.open(settings) as storage,
    ):
        logger.info("Bucket %s, Neo4j database %s", storage.bucket_name, settings.neo4j_database)
        count, objects = await relocate_objects(db, storage, apply=args.apply)
    if args.apply:
        logger.info("Relocated %d references and deleted %d keys. Verified objects/ is empty.", count, objects)
    else:
        logger.info("Would relocate %d references and delete all %d current keys under objects/.", count, objects)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    asyncio.run(main())
