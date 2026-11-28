"""Install Neo4j constraints, indexes and APOC triggers: python -m scripts.install_neo4j_schema [STEP ...]

Requires NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD and NEO4J_DATABASE (default: neo4j), via .env or the shell.
"""

import argparse
import asyncio
import logging

from neo4j import AsyncGraphDatabase, AsyncResult

from config import settings
from database.migrations import install_constraints
from database.neo4j_triggers import install_triggers

logger = logging.getLogger(__name__)

STEPS = {"constraints": install_constraints, "triggers": install_triggers}


async def main(steps: list[str]) -> None:
    if not settings.neo4j_uri or not settings.neo4j_password:
        raise SystemExit("NEO4J_URI and NEO4J_PASSWORD must be set (via .env or environment)")

    auth = (settings.neo4j_username, settings.neo4j_password)
    async with AsyncGraphDatabase.driver(settings.neo4j_uri, auth=auth) as driver:
        await driver.verify_connectivity()
        for step in steps:
            await STEPS[step](driver, settings.neo4j_database)
            logger.info("Neo4j %s installed.", step)
        if "triggers" in steps:
            invalid = await driver.execute_query(
                """MATCH (r:Recording)
                   WHERE (r)-[:RECORDING_OF]->()
                      OR count { (r)-[:FOLLOWS]->() } <> 1 OR count { (r)-[:RENDITION_OF]->() } <> 1
                      OR NOT EXISTS {
                          (r)-[:FOLLOWS]->(:Edition)-[:EDITION_OF]->(:Text)<-[:RECITATION_OF]-(:Audio)<-[:RENDITION_OF]-(r)
                      }
                   RETURN coalesce(toStringOrNull(r.id), elementId(r)) AS id LIMIT 20""",
                database_=settings.neo4j_database,
                result_transformer_=AsyncResult.value,
            )
            if invalid:
                raise SystemExit(
                    f"Recordings with incomplete or inconsistent links (up to 20 IDs): {', '.join(invalid)}. "
                    "Run the recording migration or repair the reported links before restarting the API."
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("steps", nargs="*", choices=STEPS, help="Default: all, in this order")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(name)s - %(message)s")
    logging.getLogger("neo4j").setLevel(logging.ERROR)
    asyncio.run(main(parser.parse_args().steps or list(STEPS)))
