"""Audit the configured database; use --apply during a write pause to apply upgrades."""

import argparse
import asyncio
import logging

from config import settings
from database import Database
from database.content_migration import migrate_content
from database.migrations import audit_schema, legacy_recording_ids, migrate
from storage import Storage

logger = logging.getLogger(__name__)


async def run(*, apply: bool, content: bool = False) -> None:
    async with Database.from_settings(settings) as db:
        driver = db._driver  # noqa: SLF001 - schema DDL needs the migration-owned driver
        if apply:
            await migrate(driver, settings.neo4j_database)
            logger.info("Schema upgrade completed")
        else:
            violations = await audit_schema(driver, settings.neo4j_database)
            for name, ids in violations.items():
                logger.error("%s (%d): %s", name, len(ids), ids[:20])
            if violations:
                raise SystemExit(1)
            pending = await legacy_recording_ids(driver, settings.neo4j_database)
            if pending:
                logger.info("%d legacy recording(s) will be converted by --apply: %s", len(pending), pending[:20])
            logger.info("Schema audit passed")
        if content:
            async with Storage.open(settings) as storage:
                count = await migrate_content(db, storage, apply=apply)
                logger.info("Verified %d edition content objects", count)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply migrations with application writers paused")
    parser.add_argument("--content", action="store_true", help="Also verify/backfill immutable content using S3")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(apply=args.apply, content=args.content))
