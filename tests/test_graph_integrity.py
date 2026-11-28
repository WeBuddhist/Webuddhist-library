"""Exercise catalog integrity at commit, including writes that bypass the API models."""

import asyncio

import pytest
from neo4j.exceptions import ClientError, ConstraintError

from database import audio, edition, person, text
from database.annotation import note
from database.neo4j_triggers import TRIGGERS
from models.annotation import FootnoteInput
from models.audio import AudioInput
from models.edition import EditionInput
from models.enums import AUDIO_ROLES, ContributorRole
from models.person import PersonInput
from models.text import TextInput

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def make_text(db, title, *, contributions=()):
    return await text.create(db, TextInput(
        title={"en": title}, language="en", category_id="category", contributions=list(contributions),
    ))


async def test_retyping_an_edition_cannot_create_a_second_critical_edition(test_database):
    db = test_database
    owner = await make_text(db, "Editions")
    for kind in ("critical", "collated"):
        await edition.create(db, EditionInput(type=kind), kind, owner, 10)
    with pytest.raises(ClientError, match="enforce_edition_edition_of_per_text"):
        await db.query("""
            MATCH (e:Edition {id:'collated'})-[r:HAS_TYPE]->(), (kind:EditionType {name:'critical'})
            DELETE r CREATE (e)-[:HAS_TYPE]->(kind)
        """, write=True)
    assert (await edition.get(db, "collated")).type == "collated"


async def test_concurrent_critical_edition_creates_commit_only_one(test_database):
    db = test_database
    owner = await make_text(db, "Competing editions")
    query = """
        MATCH (t:Text {id:$owner}), (kind:EditionType {name:'critical'})
        CREATE (e:Edition {id:$id, content_key:$id, revision:0, content_length:10})
               -[:EDITION_OF]->(t), (e)-[:HAS_TYPE]->(kind)
    """
    await db.query("CREATE (:EditionType {name:'critical'})", write=True)
    results = await asyncio.gather(
        *(db.query(query, write=True, owner=owner, id=id_) for id_ in ("a", "b")), return_exceptions=True,
    )
    failures = [result for result in results if isinstance(result, Exception)]
    assert len(failures) == 1, results
    assert isinstance(failures[0], ClientError) and "enforce_edition_edition_of_per_text" in str(failures[0])
    assert len(await edition.get_all(db, owner)) == 1


@pytest.mark.parametrize(("first", "second"), [
    ("TRANSLATION_OF", "COMMENTARY_OF"), ("PART_OF", "TRANSLATION_OF"), ("COMMENTARY_OF", "PART_OF"),
])
async def test_text_relationships_are_mutually_exclusive(test_database, first, second):
    db = test_database
    ids = {"source": await make_text(db, "Source"), "target": await make_text(db, "Target")}
    match = "MATCH (s:Text {id:$source}), (t:Text {id:$target}) "
    await db.query(match + f"CREATE (s)-[:{first}]->(t)", write=True, **ids)
    with pytest.raises(ClientError, match="enforce_text_"):
        await db.query(match + f"CREATE (s)-[:{second}]->(t)", write=True, **ids)


@pytest.mark.parametrize("relation", ["part_of", "commentary_of"])
async def test_part_or_commentary_cannot_be_marked_non_original(test_database, relation):
    db = test_database
    target = await make_text(db, "Target")
    text_id = await text.create(db, TextInput(
        title={"en": "Related text"}, language="en", category_id="category", **{relation: target},
    ))
    match = "MATCH (:Text {id:$id})-[r:TEXT_OF]->(:Work) "
    with pytest.raises(ClientError, match="enforce_text_has_text_of_when_"):
        await db.query(match + "SET r.original = false", write=True, id=text_id)
    record, = await db.query(match + "RETURN r.original AS original", id=text_id)
    assert record["original"] is True


@pytest.mark.parametrize("role", sorted(set(ContributorRole) - set(AUDIO_ROLES)))
async def test_audio_rechecks_role_changes_on_existing_credits(test_database, role):
    db = test_database
    credit = {"type": "ai", "id": "performer", "role": role}
    owner = await make_text(db, "Role change", contributions=[credit])
    audio_id = await audio.create(db, owner, AudioInput(contributions=[credit | {"role": "singer"}]))
    with pytest.raises(ClientError, match="enforce_contribution_with_role_when_1"):
        await db.query("""
            MATCH (:Audio {id:$id})-[:HAS_CONTRIBUTION]->(c)-[old:WITH_ROLE]->(), (r:RoleType {name:$role})
            DELETE old CREATE (c)-[:WITH_ROLE]->(r)
        """, write=True, id=audio_id, role=role)
    assert (await audio.get(db, audio_id)).contributions[0].role == "singer"


async def test_moving_a_text_credit_to_audio_rechecks_its_existing_role(test_database) -> None:
    db = test_database
    author = {"type": "ai", "id": "contributor", "role": "author"}
    text_id = await make_text(db, "Credits", contributions=[author])
    audio_id = await audio.create(db, text_id, AudioInput(contributions=[author | {"role": "narrator"}]))
    rule = next(rule for rule in TRIGGERS if rule.name == "enforce_contribution_with_role_when_1")
    async with db.get_session() as session, await session.begin_transaction() as tx:
        credit = await (await tx.run("""
            MATCH (:Text {id:$text_id})-[old:HAS_CONTRIBUTION]->(c:Contribution), (a:Audio {id:$audio_id})
            DELETE old CREATE (a)-[:HAS_CONTRIBUTION]->(c)
            RETURN elementId(c) AS id
        """, text_id=text_id, audio_id=audio_id)).single(strict=True)
        assert await (await tx.run(rule.audit)).value("violating_id") == [credit["id"]]
        with pytest.raises(ClientError, match=rule.name):
            await tx.commit()


@pytest.mark.parametrize(("first", "second"), [
    ("Text", "Text"), ("Audio", "Audio"), ("Text", "Audio"), ("Audio", "Text"),
])
async def test_contribution_cannot_gain_another_owner(test_database, first, second):
    db = test_database
    credit = {"type": "ai", "id": "shared performer", "role": "narrator"}
    owners = []
    for i, label in enumerate((first, second)):
        text_id = await make_text(db, f"Owner {i}", contributions=[credit])
        owners.append(text_id if label == "Text" else await audio.create(db, text_id, AudioInput(contributions=[credit])))
    with pytest.raises(ClientError, match="enforce_(audio|text)_has_contribution"):
        await db.query(f"""
            MATCH (:{first} {{id:$first}})-[:HAS_CONTRIBUTION]->(credit:Contribution), (other:{second} {{id:$second}})
            CREATE (other)-[:HAS_CONTRIBUTION]->(credit)
        """, write=True, first=owners[0], second=owners[1])


async def test_attached_primary_name_cannot_become_an_alternative(test_database):
    db = test_database
    owner = await person.create(db, PersonInput(name={"en": "Primary"}))
    with pytest.raises(ClientError, match="enforce_person_has_name"):
        await db.query("""
            MATCH (:Person {id:$id})-[:HAS_NAME]->(primary), (:Category {id:'category'})-[:HAS_TITLE]->(other)
            CREATE (primary)-[:ALTERNATIVE_OF]->(other)
        """, write=True, id=owner)


async def test_two_people_cannot_share_one_primary_name(test_database):
    db = test_database
    a, b = [await person.create(db, PersonInput(name={"en": name})) for name in ("First", "Second")]
    with pytest.raises(ClientError, match="enforce_person_has_name_per_nomen"):
        await db.query("""
            MATCH (:Person {id:$a})-[:HAS_NAME]->(name), (p:Person {id:$b})-[old:HAS_NAME]->()
            DELETE old CREATE (p)-[:HAS_NAME]->(name)
        """, write=True, a=a, b=b)


async def test_two_notes_cannot_share_metadata(test_database):
    db = test_database
    owner = await make_text(db, "Footnotes")
    await edition.create(db, EditionInput(type="collated"), "edition", owner, 10)
    first = await note.add_footnote(db, "edition", FootnoteInput(text="First", position=1, metadata={"name": "Private"}))
    second = await note.add_footnote(db, "edition", FootnoteInput(text="Second", position=2))
    with pytest.raises(ClientError, match="enforce_note_has_metadata_per_annotation_metadata"):
        await db.query("""
            MATCH (:Note {id:$first})-[:HAS_METADATA]->(m), (n:Note {id:$second})
            CREATE (n)-[:HAS_METADATA]->(m)
        """, write=True, first=first, second=second)


async def test_source_names_are_unique_at_commit(test_database):
    await test_database.query("CREATE (:Source {name:'Shared source'})", write=True)
    with pytest.raises(ConstraintError, match="Shared source"):
        await test_database.query("CREATE (:Source {name:'Shared source'})", write=True)
