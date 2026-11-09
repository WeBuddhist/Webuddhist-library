"""Convert recordings from the edition-attached shape into an Audio plus its Recording.

Usage:
    python -m scripts.upgrade_recordings

Deploy runs this after installing triggers and before restarting the API. It waits until the newly
installed triggers are live, converts each legacy recording in its own transaction, and logs the count.
Once every recording is converted, later runs find nothing and convert nothing.

Requires environment variables (via .env or shell):
    NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, NEO4J_DATABASE (default: neo4j)
"""

import asyncio
import logging
import sys

from neo4j import AsyncGraphDatabase

from config import settings
from database.migrations import upgrade_recordings

logger = logging.getLogger(__name__)


async def main() -> None:
    if not settings.neo4j_uri or not settings.neo4j_password:
        logger.error("NEO4J_URI and NEO4J_PASSWORD must be set (via .env or environment)")
        sys.exit(1)

    driver = AsyncGraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_username, settings.neo4j_password))
    try:
        await driver.verify_connectivity()
        converted = await upgrade_recordings(driver, settings.neo4j_database)
        logger.info("Converted %d legacy recording(s).", converted)
    finally:
        await driver.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(name)s - %(message)s")
    logging.getLogger("neo4j").setLevel(logging.ERROR)
    asyncio.run(main())
