import asyncio

import pytest
from neo4j.exceptions import ClientError, DatabaseError

from database import application, audio, category, migrations, tag
from database import edition as edition_database
from database import person as person_database
from database import text as text_database
from database.annotation import table_of_contents
from database.neo4j_triggers import TRIGGERS
from errors import DataValidationError
from identifier import generate_id
from models.audio import AudioInput
from models.category import CategoryInput
from models.tag import TagInput
from models.text import TextInput, TextOutput, TextPatch
from tests.schema_contract import EXPECTED_CONSTRAINTS, EXPECTED_INDEXES
from tests.test_content_lifecycle import fixture as create_test_edition

pytestmark = pytest.mark.asyncio(loop_scope="session")
TITLE_CONFLICT = "enforce_text_title_unique: Another text already uses this title in the same language."


@pytest.mark.parametrize("extra_link,convertible", [
    ("", True),
    ("CREATE (r)-[:RENDITION_OF]->(:Audio)", False),
    ("CREATE (r)-[:FOLLOWS]->(:Edition)", False),
    ("CREATE (r)-[:FOLLOWS]->(edition)", True),
    ("MATCH (lang:Language {code:'en'}) CREATE (r)-[:HAS_LANGUAGE]->(lang), (r)-[:HAS_LANGUAGE]->(lang)", False),
])
async def test_conversion_checks_existing_recording_links(test_database, extra_link, convertible):
    _, _, _, edition_id = await create_test_edition(test_database)
    async with test_database.get_session() as session, await session.begin_transaction() as tx:
        await tx.run("""
            MATCH (edition:Edition {id:$edition_id}), (role:RoleType {name:'narrator'})
            CREATE (r:Recording {id:'legacy', format:'mp3', size_bytes:5})-[:RECORDING_OF]->(edition),
                   (r)-[:HAS_CONTRIBUTION]->(c:Contribution)-[:BY]->(:AI {id:'performer'}),
                   (c)-[:WITH_ROLE]->(role),
                   (:Span {start:0, end:1})-[:SPAN_OF]->(:Cue {start_ms:0, end_ms:100})-[:CUE_OF]->(r)
        """ + extra_link, edition_id=edition_id)
        bad = await (await tx.run(migrations.UNCONVERTIBLE_RECORDINGS_QUERY, roles=["narrator"])).value()
        assert bad == ([] if convertible else ["legacy"])
        if convertible:
            for trigger in TRIGGERS:
                if exclusion := migrations._RECORDING_REPAIRS.get(trigger.name):
                    assert await (await tx.run(trigger.audit_query(exclusion))).value() == [], trigger.name
            await tx.run(migrations.UPGRADE_RECORDING_QUERY, recording_id="legacy", audio_id="performance")
            editions = await tx.run("MATCH (:Recording {id:'legacy'})-[:FOLLOWS]->(e) RETURN e.id")
            assert await editions.value() == [edition_id]
            await tx.commit()
        else:
            await tx.rollback()


async def test_migration_exempts_only_recording_fields_and_links_it_repairs(test_database):
    async with test_database.get_session() as session, await session.begin_transaction() as tx:
        await tx.run("""
            CREATE (r:Recording {id:'legacy', audio_key:42})-[:RECORDING_OF]->(:Edition),
                   (r)-[:HAS_LICENSE]->(:LicenseType), (r)-[:HAS_LANGUAGE]->(:Wrong),
                   (:Other {id:'legacy'})-[:HAS_LICENSE]->(:LicenseType), (:Recording {id:'modern'})
        """)
        errors = {}
        for trigger in TRIGGERS:
            if exclusion := migrations._RECORDING_REPAIRS.get(trigger.name):
                for row in await (await tx.run(trigger.audit_query(exclusion))).data():
                    errors.setdefault(row["rule"], set()).add(row["violating_id"])
        assert errors["enforce_recording_audio_key_property"] == {"legacy", "modern"}
        assert errors["enforce_recording_follows"] == {"modern"}
        assert errors["enforce_has_language_target"] == errors["enforce_has_license_target"] == {"legacy"}
        await tx.rollback()


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
        indexes = await session.run("""
            SHOW INDEXES YIELD name, type, entityType, labelsOrTypes, properties, owningConstraint
            WHERE type = 'RANGE' AND entityType = 'NODE' AND owningConstraint IS NULL
            RETURN name, labelsOrTypes, properties
        """)
        assert set(constraints) == EXPECTED_CONSTRAINTS
        assert {name: (labels, properties) async for name, labels, properties in indexes} == EXPECTED_INDEXES
        with pytest.raises(ClientError, match="already exists"):
            await (await session.run("CREATE (:Language {code:'duplicate'}), (:Language {code:'duplicate'})")).consume()


async def test_install_reports_failed_range_index_population(test_database):
    db = test_database
    await create_test_edition(db)
    await db.query("DROP INDEX segment_reference", write=True)
    try:
        await db.query("MATCH (s:Segment) SET s.reference = $ref", ref="a" * 10_000, write=True)
        with pytest.raises(DatabaseError) as error:
            await migrations.install_constraints(db._driver, db._database)
        assert error.value.code == "Neo.DatabaseError.Schema.IndexCreationFailed"
    finally:
        await db.query("DROP INDEX segment_reference IF EXISTS", write=True)
        await db.query("MATCH (s:Segment) REMOVE s.reference", write=True)
        await migrations.install_constraints(db._driver, db._database)


async def test_install_accepts_equivalent_index_and_detects_name_conflict(test_database):
    db = test_database
    await db.query("DROP INDEX segment_reference", write=True)
    try:
        await db.query("CREATE RANGE INDEX segment_reference FOR (n:Segment) ON (n.type)", write=True)
        with pytest.raises(RuntimeError, match=r"Missing range index for Segment\.reference"):
            await migrations.install_constraints(db._driver, db._database)
        await db.query("CREATE RANGE INDEX legacy_reference FOR (n:Segment) ON (n.reference)", write=True)
        await migrations.install_constraints(db._driver, db._database)
        index, = await db.query("SHOW RANGE INDEXES YIELD name, state WHERE name = 'legacy_reference'")
        assert index["state"] == "ONLINE"
    finally:
        await db.query("DROP INDEX legacy_reference IF EXISTS", write=True)
        await db.query("DROP INDEX segment_reference IF EXISTS", write=True)
        await migrations.install_constraints(db._driver, db._database)


async def test_install_detects_uniqueness_constraint_name_conflict(test_database):
    db = test_database
    await db.query("DROP CONSTRAINT source_name_unique", write=True)
    try:
        await db.query("CREATE CONSTRAINT source_name_unique FOR (n:Other) REQUIRE n.name IS UNIQUE", write=True)
        with pytest.raises(RuntimeError, match="Missing uniqueness constraints.*Source.*name"):
            await migrations.install_constraints(db._driver, db._database)
    finally:
        await db.query("DROP CONSTRAINT source_name_unique IF EXISTS", write=True)
        await migrations.install_constraints(db._driver, db._database)


async def test_migration_audits_enum_values_written_directly(test_database):
    db = test_database
    _, text_id, _, edition_id = await create_test_edition(db)
    audio_id = await audio.create(db, text_id, AudioInput(contributions=[{"type": "ai", "id": "singer", "role": "singer"}]))
    segment, = await db.query("""
        MATCH (a:Audio {id:$audio}), (e:Edition {id:$edition})-[:HAS_SEGMENTATION]->()<-[:SEGMENT_OF]-(s:Segment)
        CREATE (r:Recording {id:'rec', format:'unlisted', audio_key:'file', size_bytes:1})-[:RENDITION_OF]->(a),
               (r)-[:FOLLOWS]->(e)
        SET s.type = 'unlisted'
        RETURN s.id AS id
    """, audio=audio_id, edition=edition_id, write=True)
    lookup_labels = ["RoleType", "EditionType", "LicenseType", "NoteType", "MarkType", "BibliographyType", "AttributeType"]
    lookups = await db.query("""
        UNWIND $labels AS label CREATE (n:$(label) {name:'unlisted'}) RETURN label, elementId(n) AS id
    """, labels=lookup_labels, write=True)

    expected = {f"{row['label']}.name.enum": [row["id"]] for row in lookups}
    expected.update({"Recording.format.enum": ["rec"], "Segment.type.enum": [segment["id"]]})
    assert await migrations.audit_schema(db._driver, db._database) == expected


async def test_shared_label_does_not_merge_lookup_namespaces(test_database):
    rows = await test_database.query("""
        MATCH (n:RoleType|BibliographyType {name: 'author'})
        SET n:Lookup
        RETURN count(n) AS updated
    """, write=True)
    assert rows[0]["updated"] == 2
    await test_database.query("""
        CREATE (:Application:Imported {id:'shared', name:'example'}), (:AI:Imported {id:'shared'})
    """, write=True)
    assert await migrations.audit_schema(test_database._driver, test_database._database) == {}


async def test_legacy_source_index_upgrades_after_duplicate_audit(test_database):
    db = test_database
    await db.query("DROP CONSTRAINT source_name_unique IF EXISTS", write=True)
    await db.query("CREATE INDEX imported_source_index FOR (s:Source) ON (s.name)", write=True)
    try:
        rows = await db.query("""
            UNWIND [1, 2] AS copy CREATE (s:Source {name:'Duplicate source'})
            RETURN elementId(s) AS id
        """, write=True)
        violations = await migrations.audit_schema(db._driver, db._database)
        assert set(violations["source_name_unique"]) == {row["id"] for row in rows}
    finally:
        await db.query("MATCH (s:Source {name:'Duplicate source'}) DELETE s", write=True)
        await migrations.install_constraints(db._driver, db._database)
    await migrations.install_constraints(db._driver, db._database)
    indexes = await db.query("SHOW INDEXES YIELD name, owningConstraint WHERE name STARTS WITH 'source_name'")
    assert [row.data() for row in indexes] == [{"name": "source_name_unique", "owningConstraint": "source_name_unique"}]


@pytest.mark.parametrize("label", ["LocalizedText", "Contribution"])
async def test_idless_nodes_cannot_skip_required_relationships(test_database, label):
    async with test_database.get_session() as session:
        with pytest.raises(ClientError, match="enforce_"):
            await (await session.run(f"CREATE (:{label})")).consume()


async def test_title_uniqueness_survives_concurrent_creates_and_updates(test_database, monkeypatch):
    contend_before(monkeypatch, text_database, "validate_text_creation")
    db = test_database
    title = TextInput(title={"en": "Contended title"}, language="en", category_id="category")
    results = await asyncio.gather(text_database.create(db, title), text_database.create(db, title), return_exceptions=True)
    assert sum(isinstance(result, str) for result in results) == 1, results
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1, results
    error = errors[0]
    assert (isinstance(error, DataValidationError) and "Contended title" in str(error) and "already exists" in str(error)) or (isinstance(error, ClientError) and TITLE_CONFLICT in str(error)), results
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
        with pytest.raises(ClientError) as duplicate:
            await text_database.update(db, loser, TextPatch(title={"en": "Shared title"}))
        assert TITLE_CONFLICT in str(duplicate.value)
    else:
        assert TITLE_CONFLICT in message
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
    await db.query("""
        MATCH (:LocalizedText {text:'Same spelling'})-[tag:HAS_LANGUAGE {bcp47:'en'}]->(:Language)
        REMOVE tag.bcp47
    """, write=True)
    with pytest.raises(ClientError, match="enforce_text_title_unique"):
        await db.query("MATCH (lang:Language {code:'en'}) SET lang.code = 'en-US'", write=True)
    # Legacy imports with a malformed language tag must still be auditable.
    async with db.get_session() as session, await session.begin_transaction() as tx:
        await tx.run("MATCH (:LocalizedText {text:'Same spelling'})-[tag:HAS_LANGUAGE]->() SET tag.bcp47 = [1]")
        title_rule = next(trigger for trigger in TRIGGERS if trigger.name == "enforce_text_title_unique")
        assert await (await tx.run(title_rule.audit)).value() == []
        await tx.rollback()


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


async def test_updates_cannot_move_toc_parent_bounds_inside_a_child(test_database):
    from models.annotation import TableOfContentsInput
    from models.edition import EditionInput

    db = test_database
    text = await text_database.create(db, TextInput(title={"en": "Mutable semantics"}, language="en", category_id="category"))
    edition = generate_id()
    await edition_database.create(db, EditionInput(type="critical"), edition, text, 20)
    toc = TableOfContentsInput(sections=[{"title": {"en": "Parent"}, "span": {"start": 0, "end": 20}, "subsections": [{"title": {"en": "Child"}, "span": {"start": 5, "end": 10}}]}])
    await table_of_contents.add(db, edition, toc)
    async with db.get_session() as session:
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
