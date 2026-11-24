"""Apply Neo4j constraints and indexes: python -m scripts.install_neo4j_constraints."""

import asyncio
import logging

from neo4j import AsyncGraphDatabase

from config import settings
from database.migrations import install_constraints

logger = logging.getLogger(__name__)


async def main() -> None:
    if not settings.neo4j_uri or not settings.neo4j_password:
        raise SystemExit("NEO4J_URI and NEO4J_PASSWORD must be set (via .env or environment)")

    async with (
        AsyncGraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_username, settings.neo4j_password)
        ) as driver,
        driver.session(database=settings.neo4j_database) as session,
    ):
        await install_constraints(session)
    logger.info("Neo4j constraints and indexes installed successfully.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(name)s - %(message)s")
    asyncio.run(main())
