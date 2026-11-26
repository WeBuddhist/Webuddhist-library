"""Large tables of contents retain localization and hierarchy without per-section queries."""
import pytest
from neo4j import AsyncManagedTransaction

from database import edition as edition_database
from database import text as text_database
from database.annotation import table_of_contents
from identifier import generate_id
from models.annotation import TableOfContentsInput
from models.edition import EditionInput
from models.text import TextInput

pytestmark = pytest.mark.asyncio(loop_scope='session')

async def test_large_toc_retains_all_sections_and_localizations(test_database, monkeypatch):
    db = test_database
    text = await text_database.create(db, TextInput(title={'en': 'Large TOC'}, language='en', category_id='category'))
    edition = generate_id()
    size = 1001
    await edition_database.create(db, EditionInput(type="critical"), edition, text, size)
    children = [
        {
            "title": {"en": str(i), "bo": f"དཔེ་ཆ་ {i}"},
            "summary": {"en": f"Summary {i}"},
            "span": {"start": i, "end": i + 1},
        }
        for i in range(size)
    ]
    payload = TableOfContentsInput(sections=[{'title': {'en': 'Root'}, 'span': {'start': 0, 'end': size}, 'subsections': children}])
    run = AsyncManagedTransaction.run
    queries = []

    async def measured(self, *args, **kwargs):
        queries.append(args[0])
        return await run(self, *args, **kwargs)
    with monkeypatch.context() as context:
        context.setattr(AsyncManagedTransaction, "run", measured)
        toc = await table_of_contents.add(db, edition, payload)
    assert len(queries) < 40
    read = await table_of_contents.get(db, toc)
    assert len(read.sections[0].subsections) == size
    for actual, expected in zip(read.sections[0].subsections, payload.sections[0].subsections, strict=True):
        assert actual.title == expected.title
        assert actual.summary == expected.summary
        assert actual.span == expected.span
    await db.query("MATCH (e:Edition {id: $id}) CREATE (:TableOfContents {id: 'empty'})-[:TOC_OF]->(e)", write=True, id=edition)
    empty = await table_of_contents.get(db, "empty")
    assert empty.sections == []
    queries.clear()
    monkeypatch.setattr(AsyncManagedTransaction, "run", measured)
    assert await table_of_contents.get_all(db, edition) == sorted([empty, read], key=lambda table: table.id)
    assert sum(":TableOfContents" in query for query in queries) == 1
