"""Convert recordings from the edition-attached shape into an Audio plus its Recording."""

import asyncio

import pytest

from database.migrations import (
    audit_schema,
    legacy_recording_ids,
    migrate,
    unconvertible_recording_ids,
    upgrade_recordings,
)
from database.neo4j_triggers import SYSTEM_DATABASE_NAME, _required_rel_trigger, install_triggers, wait_for_triggers
from identifier import generate_id
from models.edition import EditionInput
from models.recording import RecordingCue
from models.text import TextInput
from tests.conftest import NEO4J_TRIGGER_REFRESH_SECONDS

pytestmark = pytest.mark.asyncio(loop_scope="session")

# The shape dev wrote before audio became its own Expression.
LEGACY_RECORDING = """
MATCH (m:Edition {id: $edition_id})
MATCH (narrator:RoleType {name: 'narrator'}), (en:Language {code: 'en'})
CREATE (r:Recording {id: $recording_id, format: 'mp3', size_bytes: 10, duration_ms: 1000, date: '2024'})
CREATE (r)-[:RECORDING_OF]->(m)
CREATE (r)-[:HAS_CONTRIBUTION]->(c:Contribution)-[:WITH_ROLE]->(narrator)
CREATE (c)-[:BY]->(:AI {id: $ai_id})
CREATE (r)-[:HAS_TITLE]->(:Nomen {id: $nomen_id})-[:HAS_LOCALIZATION]->(:LocalizedText {text: 'Legacy reading'})
    -[:HAS_LANGUAGE]->(en)
CREATE (:Span {start: 0, end: 4})-[:SPAN_OF]->(:Cue {start_ms: 0, end_ms: 500})-[:CUE_OF]->(r)
WITH r
CALL (r) {
    WHEN $with_metadata THEN {
        MATCH (license:LicenseType {name: 'cc-by'}), (bo:Language {code: 'bo'})
        CREATE (r)-[:HAS_LICENSE]->(license), (r)-[:HAS_LANGUAGE {bcp47: 'bo'}]->(bo)
    }
}
FINISH
"""

# The required-relationship trigger installed with the edition-attached shape.
OLD_RECORDING_TRIGGER = _required_rel_trigger(
    "enforce_recording_recording_of", "Recording requires RECORDING_OF", "Recording", "RECORDING_OF", "Edition"
)[0]


async def _replace_triggers_with_old_rule(db) -> None:
    async with db._driver.session(database=SYSTEM_DATABASE_NAME) as session:
        await (await session.run("CALL apoc.trigger.dropAll($database)", database=db._database)).consume()
        await (
            await session.run(
                "CALL apoc.trigger.install($database, $name, $statement, {phase: 'before'})",
                database=db._database,
                name=OLD_RECORDING_TRIGGER["name"],
                statement=f"CYPHER 25 {OLD_RECORDING_TRIGGER['query']}",
            )
        ).consume()
    await asyncio.sleep(NEO4J_TRIGGER_REFRESH_SECONDS)


async def _legacy_recording(db, *, with_metadata: bool = True) -> tuple[str, str, str]:
    text_id = await db.text.create(
        TextInput(title={"bo": f"Legacy host {generate_id()[:6]}"}, language="bo", category_id="category")
    )
    edition_id = generate_id()
    await db.edition.create(EditionInput(type="critical"), edition_id, text_id, 20)
    recording_id = generate_id()
    await _replace_triggers_with_old_rule(db)
    async with db.get_session() as session:
        await (
            await session.run(
                LEGACY_RECORDING,
                edition_id=edition_id,
                recording_id=recording_id,
                ai_id=f"tts-{generate_id()[:6]}",
                nomen_id=generate_id(),
                with_metadata=with_metadata,
            )
        ).consume()
    return text_id, edition_id, recording_id


@pytest.fixture
async def restore_triggers(test_database):
    yield
    await install_triggers(test_database._driver, test_database._database)
    await wait_for_triggers(test_database._driver, test_database._database)


@pytest.mark.usefixtures("restore_triggers")
async def test_migration_converts_legacy_recording_and_keeps_its_cues(test_database):
    db = test_database
    text_id, edition_id, recording_id = await _legacy_recording(db)
    assert await legacy_recording_ids(db._driver, db._database) == [recording_id]
    assert await audit_schema(db._driver, db._database) == {}  # Pending conversion, not a violation.

    # The old trigger is still live here; the conversion must wait for the new set instead of failing.
    await migrate(db._driver, db._database)

    assert await legacy_recording_ids(db._driver, db._database) == []
    recording = await db.recording.get(recording_id)
    assert recording.text_id == text_id
    assert recording.edition_id == edition_id
    assert recording.title is None  # The legacy title described the performance, so it moved to the Audio.
    assert recording.duration_ms == 1000
    audio = await db.audio.get(recording.audio_id)
    assert audio.text_id == text_id
    assert audio.title.root == {"en": "Legacy reading"}
    assert audio.language == "bo"
    assert audio.license == "cc-by"
    assert audio.date == "2024"
    assert audio.recordings == [recording_id]
    assert [(c.type, c.role) for c in audio.contributions] == [("ai", "narrator")]
    assert await db.recording.get_storage_location(recording_id) == f"recordings/{edition_id}/{recording_id}.mp3"
    assert await db.recording.get_cues(recording_id) == [
        RecordingCue.model_validate({"span": {"start": 0, "end": 4}, "start_ms": 0, "end_ms": 500})
    ]
    async with db.get_session() as session:
        result = await session.run(
            """
            MATCH (r:Recording {id: $id})
            RETURN r.date AS date, count { (r)-[:HAS_CONTRIBUTION|HAS_LICENSE|HAS_LANGUAGE]->() } AS moved,
                   count { (r)-[:RECORDING_OF]->() } AS legacy,
                   [(a:Audio)<-[:RENDITION_OF]-(r) | [(a)-[:AUDIO_OF]->(w) | w.id][0]][0] AS audio_work,
                   [(t:Text {id: $text_id})-[:TEXT_OF]->(w) | w.id][0] AS text_work
            """,
            id=recording_id,
            text_id=text_id,
        )
        row = await result.single()
    assert row["date"] is None
    assert row["moved"] == 0
    assert row["legacy"] == 0
    assert row["audio_work"] == row["text_work"]
    assert await audit_schema(db._driver, db._database) == {}

    assert await upgrade_recordings(db._driver, db._database) == 0
    await migrate(db._driver, db._database)
    async with db.get_session() as session:
        assert (await (await session.run("MATCH (a:Audio) RETURN count(a) AS count")).single())["count"] == 1


@pytest.mark.usefixtures("restore_triggers")
async def test_conversion_fills_missing_language_and_license(test_database):
    db = test_database
    _, _, recording_id = await _legacy_recording(db, with_metadata=False)
    await install_triggers(db._driver, db._database)

    assert await upgrade_recordings(db._driver, db._database) == 1

    audio = await db.audio.get((await db.recording.get(recording_id)).audio_id)
    assert audio.language == "bo"  # The recited text's language.
    assert audio.license == "public"


async def _create_legacy(db, edition_id: str) -> str:
    """Add another legacy recording to an edition while the old trigger set is installed."""
    recording_id = generate_id()
    async with db.get_session() as session:
        await (
            await session.run(
                LEGACY_RECORDING,
                edition_id=edition_id,
                recording_id=recording_id,
                ai_id=f"tts-{generate_id()[:6]}",
                nomen_id=generate_id(),
                with_metadata=True,
            )
        ).consume()
    return recording_id


@pytest.mark.usefixtures("restore_triggers")
async def test_conversion_handles_several_recordings_each_with_its_own_audio(test_database):
    db = test_database
    _, edition_id, first = await _legacy_recording(db)
    second = await _create_legacy(db, edition_id)
    await install_triggers(db._driver, db._database)

    assert await upgrade_recordings(db._driver, db._database) == 2

    audios = {(await db.recording.get(recording_id)).audio_id for recording_id in (first, second)}
    assert len(audios) == 2  # One performance each; recordings are never grouped.
    assert await audit_schema(db._driver, db._database) == {}


@pytest.mark.usefixtures("restore_triggers")
async def test_unconvertible_recording_is_reported_and_stops_the_upgrade_untouched(test_database):
    db = test_database
    _, edition_id, broken = await _legacy_recording(db)
    healthy = await _create_legacy(db, edition_id)
    async with db.get_session() as session:
        await (await session.run("MATCH (r:Recording {id: $id}) REMOVE r.format", id=broken)).consume()

    assert await unconvertible_recording_ids(db._driver, db._database) == [broken]
    assert (await audit_schema(db._driver, db._database)) == {"legacy_recording_unconvertible": [broken]}
    await install_triggers(db._driver, db._database)
    with pytest.raises(RuntimeError, match="cannot be converted"):
        await upgrade_recordings(db._driver, db._database)
    assert sorted(await legacy_recording_ids(db._driver, db._database)) == sorted([broken, healthy])


@pytest.mark.usefixtures("restore_triggers")
async def test_audit_hides_only_pending_recordings(test_database):
    db = test_database
    await _legacy_recording(db)
    async with db.get_session() as session:
        await (await session.run("MATCH (c:Category {id: 'category'}) CREATE (c)-[:CHILD_OF]->(c)")).consume()

    assert await audit_schema(db._driver, db._database) == {"legacy_category_relationship": ["category"]}


@pytest.mark.usefixtures("restore_triggers")
async def test_wait_for_triggers_times_out_when_the_live_set_differs(test_database):
    await _replace_triggers_with_old_rule(test_database)

    with pytest.raises(TimeoutError, match="enforce_"):
        await wait_for_triggers(test_database._driver, test_database._database, wait_seconds=0.3, interval=0.1)


async def test_upgrade_creates_the_audio_constraint(test_database):
    db = test_database
    async with db.get_session() as session:
        await (await session.run("DROP CONSTRAINT audio_id_unique IF EXISTS")).consume()

    assert await upgrade_recordings(db._driver, db._database) == 0

    async with db.get_session() as session:
        names = {r["name"] for r in await (await session.run("SHOW CONSTRAINTS")).data()}
    assert "audio_id_unique" in names


@pytest.mark.usefixtures("restore_triggers")
async def test_deploy_script_converts_with_configured_database(test_database, _neo4j_container, monkeypatch):
    from config import settings
    from scripts import upgrade_recordings as script

    db = test_database
    _, _, recording_id = await _legacy_recording(db)
    await install_triggers(db._driver, db._database)
    monkeypatch.setattr(settings, "neo4j_uri", _neo4j_container["uri"])
    monkeypatch.setattr(settings, "neo4j_username", "neo4j")
    monkeypatch.setattr(settings, "neo4j_password", _neo4j_container["password"])
    monkeypatch.setattr(settings, "neo4j_database", _neo4j_container["database"])

    await script.main()

    assert await legacy_recording_ids(db._driver, db._database) == []
    assert (await db.recording.get(recording_id)).audio_id
