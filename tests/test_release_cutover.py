from time import perf_counter

import pytest

from content_service import create_edition
from database import person as person_database
from database import text as text_database
from models.person import PersonInput
from models.requests import EditionRequestModel
from models.text import TextInput
from scripts.reindex import rebuild

pytestmark = pytest.mark.asyncio(loop_scope='session')

async def test_alias_cutover_and_rollback_retain_old_indexes(
    test_database, mock_storage, content_search, catalog_search, monkeypatch
):
    db, storage = (test_database, mock_storage)
    text = await text_database.create(db, TextInput(title={"en": "Rehearsal"}, language="en", category_id="category"))
    await create_edition(db, storage, text, EditionRequestModel(metadata={'type': 'critical'}, content='rehearsal text', segmentation={'segments': [{'lines': [{'start': 0, 'end': 14}]}]}))
    client = content_search.index._client
    catalog_alias, content_alias = (catalog_search.index.index_name, content_search.index.index_name)
    people = [await person_database.create(db, PersonInput(name={"en": name})) for name in ("First", "Second")]
    translation = await text_database.create(db, TextInput(
        title={"en": "Translation", "bo": "དཔེ་ཆ་"}, language="bo", translation_of=text,
        contributions=[{"type": "person", "id": people[0], "role": "translator"}],
    ))
    catalog_ids = {"person": sorted(people), "text": sorted([text, translation])}
    expected = [
        await catalog_search.prepare_document(kind, entity_id, db)
        for kind, ids in catalog_ids.items() for entity_id in ids
    ]
    begin = perf_counter()
    async with db.get_session() as session, await session.begin_transaction() as tx:
        # Read the legacy node in the open transaction because current triggers reject it at commit.
        await tx.run("CREATE (:Text {id: 'legacy-without-work'})")
        batches = []

        async def query(statement, **params):
            if "ids" in params:
                batches.append(params["ids"])
            return [row async for row in await tx.run(statement, **params)]

        with monkeypatch.context() as patch:
            patch.setattr(db, "query", query)
            first = await rebuild(db, storage, client, catalog_alias, content_alias)
        await tx.rollback()
    assert batches == list(catalog_ids.values())
    assert first["documents"][catalog_alias] == len(expected)
    indexed = await client.mget(index=catalog_alias, body={"ids": [doc["id"] for doc in expected]})
    assert [doc["_source"] for doc in indexed["docs"]] == expected
    second = await rebuild(db, storage, client, catalog_alias, content_alias)
    assert second["previous"] == {alias: [index] for alias, index in first["current"].items()}
    for index in first['current'].values():
        assert await client.indices.exists(index=index)
    assert await content_search.search(db=db, query="rehearsal", search_type="exact", limit=10)
    actions = [{"remove": {"index": index, "alias": alias}} for alias, index in second["current"].items()]
    actions += [{"add": {"index": index, "alias": alias}} for alias, index in first["current"].items()]
    await client.indices.update_aliases(body={"actions": actions})
    assert await content_search.search(db=db, query="rehearsal", search_type="exact", limit=10)
    print(
        f"Two replacement builds, validation, atomic switches and rollback: {perf_counter() - begin:.3f}s (tiny local fixture)"
    )
    inactive = set(second['current'].values()) | {i for indexes in first['previous'].values() for i in indexes}
    for index in inactive:
        await client.indices.delete(index=index)
