"""Apply Neo4j constraints and indexes: python -m scripts.install_neo4j_constraints."""

import asyncio
import logging
from pathlib import Path
from typing import LiteralString, cast

from neo4j import AsyncGraphDatabase

from config import settings

logger = logging.getLogger(__name__)


async def main() -> None:
    if not settings.neo4j_uri or not settings.neo4j_password:
        raise SystemExit("NEO4J_URI and NEO4J_PASSWORD must be set (via .env or environment)")

    constraints = (Path(__file__).parents[1] / "database" / "neo4j_constraints.cypher").read_text()
    async with (
        AsyncGraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_username, settings.neo4j_password)
        ) as driver,
        driver.session(database=settings.neo4j_database) as session,
    ):
        for statement in constraints.split(";"):
            if statement.strip():
                await (await session.run(cast(LiteralString, statement))).consume()
    logger.info("Neo4j constraints and indexes installed successfully.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(name)s - %(message)s")
    asyncio.run(main())
