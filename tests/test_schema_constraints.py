import asyncio

import pytest
from neo4j.exceptions import ClientError

from database import application, audio, category, tag
from database import edition as edition_database
from database import person as person_database
from database import text as text_database
from database.annotation import table_of_contents
from errors import DataValidationError
from identifier import generate_id
from models.category import CategoryInput
from models.tag import TagInput
from models.text import TextInput, TextOutput, TextPatch
from tests.schema_contract import EXPECTED_CONSTRAINTS, EXPECTED_INDEXES

pytestmark = pytest.mark.asyncio(loop_scope="session")


def contend_before(monkeypatch, target, method):
    original = getattr(target, method)
    barrier = asyncio.Barrier(2)
    arrivals = 0
    async def simultaneous(*args, **kwargs):
        nonlocal arrivals
        arrivals += 1
        if arrivals <= 2:
            await asyncio.wait_for(barrier.wait(), timeout=10)
        return await original(*args, **kwargs)
    monkeypatch.setattr(target, method, simultaneous)


async def test_installed_graph_schema(test_database):
    async with test_database.get_session() as session:
        constraints = await (await session.run("SHOW CONSTRAINTS YIELD name RETURN name")).value()
        indexes = await (await session.run("SHOW INDEXES YIELD name RETURN name")).value()
        assert set(constraints) == EXPECTED_CONSTRAINTS
        assert EXPECTED_INDEXES <= set(indexes)
        with pytest.raises(ClientError, match="already exists"):
            await (await session.run("CREATE (:Language {code:'duplicate'}), (:Language {code:'duplicate'})")).consume()


@pytest.mark.parametrize("label", ["LocalizedText", "Contribution"])
async def test_idless_nodes_cannot_skip_required_relationships(test_database, label):
    async with test_database.get_session() as session:
        with pytest.raises(ClientError, match="enforce_"):
            await (await session.run(f"CREATE (:{label})")).consume()


async def test_updates_cannot_invert_spans(test_database):
    db = test_database
    text = await text_database.create(db, TextInput(title={"en": "Mutable spans"}, language="en", category_id="category"))
    from models.annotation import SegmentationInput
    from models.edition import EditionInput
    edition = generate_id()
    await edition_database.create(db, EditionInput(type="critical"), edition, text, 20,
                            segmentation=SegmentationInput(segments=[{"lines": [{"start": 2, "end": 5}]}]))
    async with db.get_session() as session:
        with pytest.raises(ClientError, match="enforce_span_start_lte_end"):
            await (await session.run("MATCH (s:Span) SET s.start = 8")).consume()


async def test_title_uniqueness_survives_concurrent_creates_and_updates(test_database, monkeypatch):
    contend_before(monkeypatch, text_database, "validate_text_creation")
    db = test_database
    title = TextInput(title={"en": "Contended title"}, language="en", category_id="category")
    results = await asyncio.gather(text_database.create(db, title), text_database.create(db, title), return_exceptions=True)
    assert sum(isinstance(result, str) for result in results) == 1, results
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1, results
    error = errors[0]
    assert (isinstance(error, DataValidationError) and "Contended title" in str(error) and "already exists" in str(error)) or (isinstance(error, ClientError) and "enforce_text_title_unique: Duplicate title+language exists" in str(error)), results
    second = await text_database.create(db, TextInput(title={"en": "Second title"}, language="en", category_id="category"))
    async with db.get_session() as session:
        with pytest.raises(ClientError, match="enforce_text_title_unique"):
            await (await session.run("MATCH (:Text {id: $id})-[:HAS_TITLE]->(:Nomen)-[:HAS_LOCALIZATION]->(t:LocalizedText) SET t.text = 'Contended title'", id=second)).consume()


async def test_concurrent_title_changes_cannot_claim_the_same_title(test_database, monkeypatch):
    contend_before(monkeypatch, text_database, "_get_for_update")
    db = test_database
    ids = [await text_database.create(db, TextInput(title={"en": title}, language="en", category_id="category"))
           for title in ("First title", "Second title")]
    results = await asyncio.gather(
        *(text_database.update(db, text_id, TextPatch(title={"en": "Shared title"})) for text_id in ids),
        return_exceptions=True,
    )
    assert sum(isinstance(result, TextOutput) for result in results) == 1, results
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1 and isinstance(errors[0], ClientError), results
    message = str(errors[0])
    if "will wait indefinitely" in message:
        # APOC may wrap a lock conflict as a ClientError. A fresh attempt must
        # then reject the duplicate title, rather than passing on any error.
        assert "enforce_text_title_unique" in message
        assert errors[0].code == "Neo.ClientError.Transaction.TransactionHookFailed"
        loser = next(text_id for text_id, result in zip(ids, results) if isinstance(result, Exception))
        with pytest.raises(ClientError, match="enforce_text_title_unique: Duplicate title\\+language exists"):
            await text_database.update(db, loser, TextPatch(title={"en": "Shared title"}))
    else:
        assert "enforce_text_title_unique: Duplicate title+language exists" in message
    titles = [(await text_database.get(db, text_id)).title.root for text_id in ids]
    assert titles.count({"en": "Shared title"}) == 1
    for index, result in enumerate(results):
        if isinstance(result, Exception):
            assert titles[index] == {"en": ("First title", "Second title")[index]}


async def test_title_language_relationship_updates_cannot_create_duplicates(test_database):
    db = test_database
    for language in ("en", "en-US"):
        text_id = await text_database.create(db, TextInput(
            title={language: "Same spelling"}, language="en", category_id="category",
        ))
    async with db.get_session() as session:
        with pytest.raises(ClientError, match="enforce_text_title_unique"):
            await (await session.run("""
                MATCH (:Text {id: $id})-[:HAS_TITLE]->(:Nomen)-[:HAS_LOCALIZATION]->(:LocalizedText)
                      -[language:HAS_LANGUAGE]->(:Language)
                SET language.bcp47 = 'EN'
            """, id=text_id)).consume()
    assert (await text_database.get(db, text_id)).title.root == {"en-US": "Same spelling"}


@pytest.mark.parametrize("entity,model", [("tag", TagInput), ("category", CategoryInput)])
async def test_taxonomy_title_check_serializes_with_create(test_database, entity, model, monkeypatch):
    module = {"tag": tag, "category": category}[entity]
    contend_before(monkeypatch, module, '_validate_not_exists')
    value = model(title={"en": "Contended"})
    results = await asyncio.gather(module.create(test_database, value, "test_application"), module.create(test_database, value, "test_application"), return_exceptions=True)
    assert sum(isinstance(result, str) for result in results) == 1, results
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1 and isinstance(errors[0], DataValidationError), results
    async with test_database.get_session() as session:
        assert (await (await session.run(f"MATCH (n:{entity.title()})-[:HAS_TITLE]->(:Nomen)-[:HAS_LOCALIZATION]->(title:LocalizedText {{text: 'Contended'}}) RETURN count(n) AS count")).single())["count"] == 1


async def test_updates_cannot_change_audio_role_or_toc_parent_bounds(test_database):
    from models.annotation import TableOfContentsInput
    from models.audio import AudioInput
    from models.edition import EditionInput

    db = test_database
    text = await text_database.create(db, TextInput(title={"en": "Mutable semantics"}, language="en", category_id="category"))
    edition = generate_id()
    await edition_database.create(db, EditionInput(type="critical"), edition, text, 20)
    await audio.create(db, text, AudioInput(contributions=[{"type": "ai", "id": "tts", "role": "narrator"}]))
    toc = TableOfContentsInput(sections=[{"title": {"en": "Parent"}, "span": {"start": 0, "end": 20}, "subsections": [{"title": {"en": "Child"}, "span": {"start": 5, "end": 10}}]}])
    await table_of_contents.add(db, edition, toc)
    async with db.get_session() as session:
        with pytest.raises(ClientError, match="enforce_audio_contribution_role"):
            await (await session.run("MATCH (r:RoleType {name: 'narrator'}) SET r.name = 'unsupported-role'")).consume()
        with pytest.raises(ClientError, match="enforce_toc_hierarchy"):
            await (await session.run("MATCH (:TableOfContentsSection)-[:SUBSECTION_OF]->(parent)<-[:SPAN_OF]-(s:Span) SET s.end = 8")).consume()


async def test_installed_trigger_rejects_relationship_deletion_and_rolls_back(test_database):
    from models.person import PersonInput
    db = test_database
    person = await person_database.create(db, PersonInput(name={"en": "Required name"}))
    original = await person_database.get(db, person)
    async with db.get_session() as session:
        with pytest.raises(ClientError, match='enforce_person_has_name'):
            await (await session.run('MATCH (:Person {id:$id})-[r:HAS_NAME]->() DELETE r',id=person)).consume()
    assert await person_database.get(db, person) == original


@pytest.mark.parametrize('rows', [0,2])
async def test_invalid_create_result_rolls_back_real_transaction(test_database, monkeypatch, rows):
    from neo4j.exceptions import ResultNotSingleError
    db = test_database
    query = f'CREATE (a:Application {{id:$application_id,name:$name}}) WITH a UNWIND {list(range(rows))} AS duplicate RETURN a.id AS id'
    monkeypatch.setattr(application, 'CREATE_QUERY', query)
    with pytest.raises(ResultNotSingleError):
        await application.create(db, "unpublished", "Unpublished")
    assert not await application.exists(db, 'unpublished')
