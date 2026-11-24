"""Build replacement projections during a write pause and switch both aliases together."""

import argparse
import asyncio
import logging
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, LiteralString
from uuid import uuid4

from catalog_search import CatalogSearchService, catalog_index_body
from config import check_environment, settings
from content_search import ContentSearchService, content_index_body
from database import Database
from search_client import create_search_client
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
        after = ""
        while True:
            async with db.get_session() as session:
                result = await session.run(
                    f"MATCH (n:{label}) WHERE n.id > $after RETURN n.id AS id ORDER BY id LIMIT 100",
                    after=after,
                )
                ids = await result.value("id")
            if not ids:
                break
            if kind == "edition":
                for edition_id in ids:
                    chunks = await content.prepare_documents(edition_id, db, storage)
                    counts[content_alias] += await content.index.bulk_index(chunks, refresh=False)
            else:
                documents = [await catalog.prepare_document(kind, entity_id, db) for entity_id in ids]
                counts[catalog_alias] += await catalog.index.bulk_index(
                    (doc for doc in documents if not doc.get("deleted")), refresh=False
                )
            after = ids[-1]
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
    async with AsyncExitStack() as stack:
        db = await stack.enter_async_context(Database.from_settings(settings))
        storage = await stack.enter_async_context(Storage.open(settings.aws_s3_bucket, settings.aws_region))
        client = create_search_client(
            endpoint=settings.opensearch_endpoint,
            region=settings.aws_region,
            auth_mode=settings.opensearch_auth_mode,
            username=settings.opensearch_username,
            password=settings.opensearch_password,
        )
        stack.push_async_callback(client.close)
        result = await rebuild(db, storage, client, settings.opensearch_catalog_index, settings.opensearch_index)
        logger.info("Cutover: %s", result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
