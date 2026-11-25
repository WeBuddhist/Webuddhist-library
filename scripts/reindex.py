"""Build replacement projections during a write pause and switch both aliases together."""

import argparse
import asyncio
import logging
from typing import TYPE_CHECKING, LiteralString
from uuid import uuid4

from config import check_environment, settings
from database import Database
from database.repository import iter_id_batches
from search.catalog import CatalogSearchService
from search.catalog_mapping import catalog_index_body
from search.client import create_search_client
from search.content import ContentSearchService
from search.content_mapping import content_index_body
from storage import Storage

if TYPE_CHECKING:
    from opensearchpy import AsyncOpenSearch

logger = logging.getLogger(__name__)


async def rebuild(
    db: Database, storage: Storage, client: AsyncOpenSearch, catalog_alias: str, content_alias: str
) -> dict:
    """Manually rebuild from canonical records with API/import writers stopped."""
    old = {}
    concrete = set()
    for alias in (catalog_alias, content_alias):
        if await client.indices.exists_alias(name=alias):
            old[alias] = list(await client.indices.get_alias(name=alias))
        else:
            old[alias] = []
            if await client.indices.exists(index=alias):
                concrete.add(alias)
    suffix = uuid4().hex
    names = {alias: f"{alias}-{suffix}" for alias in old}
    await client.indices.create(index=names[catalog_alias], body=catalog_index_body())
    await client.indices.create(index=names[content_alias], body=content_index_body())
    catalog = CatalogSearchService(client=client, index_name=names[catalog_alias])
    content = ContentSearchService(client=client, index_name=names[content_alias])
    counts = await _populate(db, storage, catalog, content, catalog_alias=catalog_alias, content_alias=content_alias)
    for alias, index in names.items():
        await client.indices.refresh(index=index)
        actual = (await client.count(index=index))["count"]
        if actual != counts[alias]:
            raise RuntimeError(f"Replacement {index} has {actual} documents; expected {counts[alias]}")
    actions = [{"remove": {"index": index, "alias": alias}} for alias, indexes in old.items() for index in indexes]
    # A concrete index occupies its name. Replace it with the same-named alias only
    # after both replacement indexes have been populated and checked.
    actions += [{"remove_index": {"index": index}} for index in sorted(concrete)]
    actions += [{"add": {"index": index, "alias": alias}} for alias, index in names.items()]
    if concrete:
        logger.warning("Replacing concrete indexes with aliases of the same names: %s", sorted(concrete))
    await client.indices.update_aliases(body={"actions": actions})
    return {"previous": old, "current": names, "documents": counts, "replaced_concrete": sorted(concrete)}


async def _populate(
    db: Database,
    storage: Storage,
    catalog: CatalogSearchService,
    content: ContentSearchService,
    *,
    catalog_alias: str,
    content_alias: str,
) -> dict[str, int]:
    counts = {catalog_alias: 0, content_alias: 0}
    labels: dict[str, LiteralString] = {"person": "Person", "text": "Text", "edition": "Edition"}
    for kind, label in labels.items():
        async for rows in iter_id_batches(db, f"MATCH (n:{label}) WHERE n.id > $after RETURN n.id AS id"):
            if kind == "edition":
                for row in rows:
                    chunks = await content.prepare_documents(row["id"], db, storage)
                    counts[content_alias] += await content.index.bulk_index(chunks, refresh=False)
            else:
                documents = [await catalog.prepare_document(kind, row["id"], db) for row in rows]
                counts[catalog_alias] += await catalog.index.bulk_index(
                    (doc for doc in documents if not doc.get("deleted")), refresh=False
                )
    return counts


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--writes-paused",
        action="store_true",
        required=True,
        help="Confirm API/import writers are stopped",
    )
    parser.parse_args()
    check_environment(settings)
    async with (
        Database.from_settings(settings) as db,
        Storage.open(settings) as storage,
        create_search_client(settings) as client,
    ):
        result = await rebuild(db, storage, client, settings.opensearch_catalog_index, settings.opensearch_index)
        logger.info("Cutover: %s", result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
