"""Excerpt editions: a text served through spans of another edition, without copying content."""
import json

import pytest
from neo4j.exceptions import ClientError

from database import person as person_database
from database import migrations, text
from database.content_state import read_state
from identifier import generate_id
from models.base import LocalizedString
from models.person import PersonInput
from models.text import TextInput
from tests.helpers import create_unauthenticated_app

pytestmark = pytest.mark.asyncio(loop_scope="session")

WHOLE = "One. Two. Three."
SECTIONS = [(0, 5), (5, 10), (10, 16)]


async def _text(db, title: str, part_of: str | None = None) -> str:
    return await text.create(db, TextInput(title={"en": title}, language="en", category_id="category", part_of=part_of))


async def _stored(client, text_id: str, content: str, sections: list[tuple[int, int]]) -> str:
    segments = [{"lines": [{"start": start, "end": end}]} for start, end in sections]
    response = await client.post(
        f"/v2/texts/{text_id}/editions",
        json={"metadata": {"type": "critical"}, "content": content, "segmentation": {"segments": segments}},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _span(start: int, end: int) -> dict:
    return {"start": start, "end": end}


def _spans(*ranges: tuple[int, int]) -> list[dict]:
    return [_span(*bounds) for bounds in ranges]


async def _excerpt(client, text_id: str, edition_id: str, *ranges: tuple[int, int]):
    return await client.post(
        f"/v2/texts/{text_id}/editions",
        json={"metadata": {"type": "excerpt"}, "excerpt_of": {"edition_id": edition_id, "spans": _spans(*ranges)}},
    )


async def _part(client, db) -> tuple[str, str, str, str]:
    """The second chapter of a text, as a text of its own."""
    whole = await _text(db, "Whole")
    source = await _stored(client, whole, WHOLE, SECTIONS)
    part = await _text(db, "Two", part_of=whole)
    response = await _excerpt(client, part, source, (5, 10))
    assert response.status_code == 201, response.text
    return whole, source, part, response.json()["id"]


async def _content(client, edition_id: str, **params) -> str:
    response = await client.get(f"/v2/editions/{edition_id}/content", params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def _segments(client, edition_id: str) -> list[dict]:
    response = await client.get(f"/v2/editions/{edition_id}/segmentation/segments", params={"limit": 100})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _lines(segments: list[dict]) -> list[tuple[int, int]]:
    return [(line["start"], line["end"]) for segment in segments for line in segment["lines"]]


async def _patch(client, edition_id: str, operation: dict):
    return await client.patch(f"/v2/editions/{edition_id}/content", json=operation)


async def test_part_text_reads_through_its_excerpt(client, test_database):
    whole, source, part, edition = await _part(client, test_database)
    response = await client.post(
        f"/v2/editions/{source}/table-of-contents",
        json={
            "sections": [
                {"title": {"en": "One"}, "span": {"start": 0, "end": 5}},
                {
                    "title": {"en": "Two"},
                    "span": {"start": 5, "end": 10},
                    "subsections": [{"title": {"en": "Two a"}, "span": {"start": 5, "end": 8}}],
                },
                {"title": {"en": "Three"}, "span": {"start": 10, "end": 16}},
            ]
        },
    )
    assert response.status_code == 201, response.text

    metadata = (await client.get(f"/v2/editions/{edition}")).json()
    assert metadata["type"] == "excerpt"
    assert metadata["excerpt_of"] == {"edition_id": source, "spans": _spans((5, 10))}
    assert await _content(client, edition) == "Two. "
    assert await _content(client, edition, span_start=1, span_end=3) == "wo"

    source_segments = await _segments(client, source)
    segments = await _segments(client, edition)
    assert [segment["id"] for segment in segments] == [source_segments[1]["id"]]
    assert _lines(segments) == [(0, 5)]
    segmentation = (await client.get(f"/v2/editions/{edition}/segmentation")).json()
    assert segmentation == {"id": edition, "edition_id": edition, "text_id": part}

    tocs = (await client.get(f"/v2/editions/{edition}/table-of-contents")).json()
    assert [(toc["edition_id"], toc["text_id"]) for toc in tocs] == [(edition, part)]
    [section] = tocs[0]["sections"]
    assert (section["title"], section["span"]) == ({"en": "Two"}, {"start": 0, "end": 5})
    assert [sub["span"] for sub in section["subsections"]] == [{"start": 0, "end": 3}]

    assert (await client.get(f"/v2/texts/{whole}")).json()["parts"] == [part]
    assert (await client.get(f"/v2/texts/{part}")).json()["part_of"] == whole


async def test_annotations_are_projected_into_the_spans(client, test_database):
    source = await _stored(client, await _text(test_database, "Whole"), WHOLE, SECTIONS)
    selection = await _text(test_database, "Selection")
    response = await _excerpt(client, selection, source, (0, 5), (10, 16))
    assert response.status_code == 201, response.text
    edition = response.json()["id"]
    assert await _content(client, edition) == "One. Three."

    for path, body in (
        ("yigchungs", [{"span": _span(3, 12)}]),
        ("bibliographic", {"span": _span(10, 16), "type": "title"}),
        ("durchens", [{"span": _span(1, 3), "text": "inside"}, {"span": _span(8, 12), "text": "cut"}]),
        ("footnotes", [{"position": p, "text": f"at {p}"} for p in (2, 5, 10, 16)]),
    ):
        assert (await client.post(f"/v2/editions/{source}/{path}", json=body)).status_code == 201

    async def read(path: str) -> list[dict]:
        response = await client.get(f"/v2/editions/{edition}/{path}")
        assert response.status_code == 200, response.text
        annotations = response.json()
        assert {(a["edition_id"], a["text_id"]) for a in annotations} == {(edition, selection)}
        return annotations

    assert [mark["span"] for mark in await read("yigchungs")] == [_span(3, 5), _span(5, 7)]
    assert [(b["type"], b["span"]) for b in await read("bibliographic")] == [("title", _span(5, 11))]
    assert [(note["text"], note["span"]) for note in await read("durchens")] == [("inside", _span(1, 3))]
    footnotes = [(note["text"], note["position"]) for note in await read("footnotes")]
    assert footnotes == [("at 2", 2), ("at 10", 5), ("at 16", 11)]


async def test_spans_join_in_listed_order(client, test_database):
    source = await _stored(client, await _text(test_database, "Whole"), WHOLE, SECTIONS)

    response = await _excerpt(client, await _text(test_database, "Three, One"), source, (10, 16), (0, 5))

    assert response.status_code == 201, response.text
    edition = response.json()["id"]
    assert await _content(client, edition) == "Three.One. "
    assert await _content(client, edition, span_start=4, span_end=8) == "e.On"
    assert await _content(client, edition, span_start=4) == "Three.One. "
    assert await _content(client, edition, span_end=8) == "Three.One. "
    assert _lines(await _segments(client, edition)) == [(0, 6), (6, 11)]
    assert (await test_database.read(read_state, edition)).length == 11


async def test_part_of_a_part_is_stored_as_spans_of_the_source(client, test_database):
    whole = await _text(test_database, "Whole")
    source = await _stored(client, whole, WHOLE, SECTIONS)
    selection = await _text(test_database, "One and Three", part_of=whole)
    response = await _excerpt(client, selection, source, (0, 5), (10, 16))
    assert response.status_code == 201, response.text
    selection_edition = response.json()["id"]
    verse = await _text(test_database, "Verse", part_of=selection)

    response = await _excerpt(client, verse, selection_edition, (3, 8))

    assert response.status_code == 201, response.text
    edition = response.json()["id"]
    assert await _content(client, edition) == ". Thr"
    excerpt_of = (await client.get(f"/v2/editions/{edition}")).json()["excerpt_of"]
    assert excerpt_of == {"edition_id": source, "spans": _spans((3, 5), (10, 13))}
    assert (await _excerpt(client, verse, source, (0, 2))).status_code == 422  # not an edition of the selection
    assert (await client.delete(f"/v2/editions/{selection_edition}")).status_code == 204
    assert await _content(client, edition) == ". Thr"


PROJECTED_READS = {
    "/content",
    "/segmentation",
    "/segmentation/segments",
    "/table-of-contents",
    "/bibliographic",
    "/durchens",
    "/footnotes",
    "/yigchungs",
}
UNPROJECTED_READS = {
    "": "the excerpt edition's own metadata",
    "/recordings": "recordings follow the excerpt edition itself",
    "/content/url": "409: an excerpt edition stores no content object",
    "/related": "text relationships only; alignments are not projected yet",
    "/alignments": "not projected yet",
    "/alignments/{target_edition_id}": "not projected yet",
    "/pagination": "not projected yet",
}


async def test_every_edition_read_is_projected_or_listed_as_not(client, test_database):
    routes = {
        path.split("}", 1)[1]
        for path, operations in create_unauthenticated_app().openapi()["paths"].items()
        if "get" in operations and path.startswith("/v2/editions/{")
    }
    assert routes == PROJECTED_READS | UNPROJECTED_READS.keys()

    _, source, _, edition = await _part(client, test_database)
    span = _span(5, 10)
    for path, body in (
        ("table-of-contents", {"sections": [{"title": {"en": "Two"}, "span": span}]}),
        ("bibliographic", {"span": span, "type": "title"}),
        ("durchens", [{"span": span, "text": "note"}]),
        ("footnotes", [{"position": 7, "text": "note"}]),
        ("yigchungs", [{"span": span}]),
    ):
        assert (await client.post(f"/v2/editions/{source}/{path}", json=body)).status_code == 201
    for path in sorted(PROJECTED_READS):
        response = await client.get(f"/v2/editions/{edition}{path}")
        assert response.status_code == 200, path
        payload = response.json()
        assert payload["items"] if path.endswith("/segments") else payload, path


async def test_source_edits_flow_into_content_spans_and_cues(client, test_database):
    _, source, part, edition = await _part(client, test_database)
    person = await person_database.create(
        test_database, PersonInput(name=LocalizedString({"en": "Singer"}), bdrc=f"P{generate_id()[:8]}")
    )
    audio = await client.post(
        f"/v2/texts/{part}/audios", json={"contributions": [{"type": "person", "id": person, "role": "singer"}]}
    )
    recording = await client.post(
        f"/v2/audios/{audio.json()['id']}/recordings",
        data={"metadata": json.dumps({"edition_id": edition})},
        files={"file": ("reading.mp3", b"ID3\x04\x00\x00\x00\x00\x00\x00fake", "audio/mpeg")},
    )
    assert recording.status_code == 201, recording.text
    cues_url = f"/v2/recordings/{recording.json()['id']}/cues"
    cues = [{"span": _span(0, 3), "start_ms": 0, "end_ms": 500}, {"span": _span(3, 5), "start_ms": 500, "end_ms": 900}]
    assert (await client.put(cues_url, json=cues)).status_code in {200, 204}

    async def assert_part(content: str, span: tuple[int, int], cue_spans: list[tuple[int, int]]) -> None:
        assert await _content(client, edition) == content
        assert (await client.get(f"/v2/editions/{edition}")).json()["excerpt_of"]["spans"] == [_span(*span)]
        assert [(cue["span"]["start"], cue["span"]["end"]) for cue in (await client.get(cues_url)).json()] == cue_spans
        assert (await test_database.read(read_state, edition)).length == len(content)

    assert (await _patch(client, source, {"type": "insert", "position": 6, "text": "X"})).status_code == 204
    await assert_part("TXwo. ", (5, 11), [(0, 4), (4, 6)])

    assert (await _patch(client, source, {"type": "insert", "position": 0, "text": "Zero. "})).status_code == 204
    await assert_part("TXwo. ", (11, 17), [(0, 4), (4, 6)])

    assert (await _patch(client, source, {"type": "delete", "start": 10, "end": 12})).status_code == 204
    await assert_part("Xwo. ", (10, 15), [(0, 3), (3, 5)])
    assert await _content(client, source) == "Zero. One.Xwo. Three."

    # The slice keeps its bounds, but the replacement fully covers the second cue.
    assert (await _patch(client, source, {"type": "replace", "start": 13, "end": 15, "text": "!?"})).status_code == 204
    await assert_part("Xwo!?", (10, 15), [(0, 3)])


async def test_replace_across_adjacent_excerpt_slices_clips_only_ranged_notes(client, test_database):
    source = await _stored(client, await _text(test_database, "Whole"), "abcdefghijkl", [(0, 4), (4, 8), (8, 12)])
    excerpt = await _excerpt(client, await _text(test_database, "Selection"), source, (2, 6), (6, 10))
    assert excerpt.status_code == 201, excerpt.text
    base = f"/v2/editions/{source}"
    notes = [{"span": _span(2, 6), "text": "left"}, {"span": _span(6, 10), "text": "right"}]
    assert (await client.post(f"{base}/durchens", json=notes)).status_code == 201
    assert (await client.post(f"{base}/yigchungs", json=[{"span": _span(2, 6)}])).status_code == 201

    response = await _patch(client, source, {"type": "replace", "start": 4, "end": 8, "text": "XY"})
    assert response.status_code == 204, response.text
    assert await _content(client, excerpt.json()["id"]) == "cdXYij"
    assert [mark["span"] for mark in (await client.get(f"{base}/yigchungs")).json()] == [_span(2, 6)]
    assert {note["text"]: note["span"] for note in (await client.get(f"{base}/durchens")).json()} == {
        "left": _span(2, 4), "right": _span(6, 8),
    }


async def test_edit_that_would_remove_a_span_is_rejected(client, test_database):
    _, source, _, edition = await _part(client, test_database)

    response = await _patch(client, source, {"type": "delete", "start": 4, "end": 11})

    assert response.status_code == 409
    assert await _content(client, source) == WHOLE
    assert await _content(client, edition) == "Two. "


async def test_identical_source_replacement_preserves_excerpt_slice(client, test_database):
    _, source, _, edition = await _part(client, test_database)
    response = await _patch(client, source, {"type": "replace", "start": 5, "end": 10, "text": WHOLE[5:10]})
    assert response.status_code == 204, response.text
    assert await _content(client, edition) == WHOLE[5:10]
    assert (await client.get(f"/v2/editions/{edition}")).json()["excerpt_of"]["spans"] == _spans((5, 10))


async def test_excerpt_edition_rejects_its_own_annotations_and_content_writes(client, test_database):
    _, _, _, edition = await _part(client, test_database)
    span = _span(0, 5)

    writes = [
        client.post(f"/v2/editions/{edition}/segmentation", json={"segments": [{"lines": [span]}]}),
        client.post(
            f"/v2/editions/{edition}/table-of-contents", json={"sections": [{"title": {"en": "T"}, "span": span}]}
        ),
        client.post(f"/v2/editions/{edition}/footnotes", json=[{"position": 0, "text": "note"}]),
        _patch(client, edition, {"type": "insert", "position": 0, "text": "X"}),
        client.get(f"/v2/editions/{edition}/content/url"),
    ]
    for write in writes:
        assert (await write).status_code == 409
    assert await _content(client, edition) == "Two. "


async def test_deletes_keep_excerpt_editions_resolvable(client, test_database):
    whole, source, part, edition = await _part(client, test_database)

    assert (await client.delete(f"/v2/editions/{source}")).status_code == 409
    response = await client.delete(f"/v2/texts/{whole}")
    assert response.status_code == 409
    assert "part(s)" in response.text

    assert (await client.delete(f"/v2/editions/{edition}")).status_code == 204
    assert (await client.delete(f"/v2/editions/{source}")).status_code == 204
    assert (await client.delete(f"/v2/texts/{part}")).status_code == 204
    async with test_database.get_session() as session:
        record = await (await session.run("MATCH (n:Span) RETURN count(n) AS count")).single()
    assert record["count"] == 0


async def test_edition_content_keys_and_migration_audit(client, test_database):
    _, source, _, excerpt = await _part(client, test_database)
    assert (await test_database.read(read_state, source)).object_key
    assert (await test_database.read(read_state, excerpt)).object_key is None

    audit = migrations.AUDITS["enforce_excerpt_edition"]
    async with test_database.get_session() as session, await session.begin_transaction() as tx:
        assert await (await tx.run(audit)).value() == []
        await tx.run("""
            MATCH (s:Edition {id: $source}), (e:Edition {id: $excerpt})
            SET e.content_key = s.content_key REMOVE s.content_key
        """, source=source, excerpt=excerpt)
        assert set(await (await tx.run(audit)).value()) == {source, excerpt}
        await tx.rollback()


async def test_source_can_become_an_excerpt_only_after_its_dependents_are_gone(client, test_database):
    text_id = await _text(test_database, "Source")
    response = await client.post(
        f"/v2/texts/{text_id}/editions", json={"metadata": {"type": "collated"}, "content": WHOLE}
    )
    assert response.status_code == 201, response.text
    source = response.json()["id"]
    target = await _stored(client, await _text(test_database, "New source"), WHOLE, SECTIONS)
    excerpt = await _excerpt(client, await _text(test_database, "Selection"), source, (5, 10))
    assert excerpt.status_code == 201, excerpt.text
    dependent = excerpt.json()["id"]

    convert = """
        MATCH (e:Edition {id: $source})-[kind:HAS_TYPE]->(), (target:Edition {id: $target}),
              (excerpt:EditionType {name: 'excerpt'})
        DELETE kind
        REMOVE e.content_key
        CREATE (e)-[:HAS_TYPE]->(excerpt), (e)-[:EXCERPT_OF]->(target),
               (:Span {start: 0, end: e.content_length, index: 0})-[:SPAN_OF]->(e)
    """
    with pytest.raises(ClientError, match="enforce_edition_excerpt_of_when_2_max_zero"):
        await test_database.query(convert, write=True, source=source, target=target)
    assert await _content(client, dependent) == "Two. "

    assert (await client.delete(f"/v2/editions/{dependent}")).status_code == 204
    await test_database.query(convert, write=True, source=source, target=target)
    assert await _content(client, source) == WHOLE


async def test_renaming_excerpt_type_rechecks_its_editions(client, test_database):
    _, _, _, excerpt = await _part(client, test_database)
    with pytest.raises(ClientError, match="enforce_edition_has_type_when_[12]"):
        await test_database.query("MATCH (t:EditionType {name: 'excerpt'}) SET t.name = 'collated'", write=True)
    assert await _content(client, excerpt) == "Two. "


@pytest.mark.parametrize(("edit", "rule"), [
    ("MATCH (e)-[r:EXCERPT_OF]->() DELETE r", "enforce_edition_excerpt_of_when_1"),
    ("MATCH (s:Span)-[:SPAN_OF]->(e) DETACH DELETE s", "enforce_span_span_of_per_edition_when_1"),
])
async def test_excerpt_cannot_lose_its_source_or_last_span(client, test_database, edit, rule):
    _, _, _, excerpt = await _part(client, test_database)
    with pytest.raises(ClientError, match=f"{rule}.*{excerpt}"):
        await test_database.query(f"MATCH (e:Edition {{id:$id}}) {edit}", write=True, id=excerpt)
    assert await _content(client, excerpt) == "Two. "


async def test_invalid_excerpt_editions_are_rejected(client, test_database):
    whole, source, part, edition = await _part(client, test_database)
    other = await _stored(client, await _text(test_database, "Other"), "Other text.", [(0, 11)])
    excerpt_of = {"edition_id": source, "spans": _spans((0, 5))}

    for body in (
        {"metadata": {"type": "excerpt"}, "content": "x", "excerpt_of": excerpt_of},
        {"metadata": {"type": "critical"}, "excerpt_of": excerpt_of},
        {"metadata": {"type": "excerpt"}, "content": "x"},
        {"metadata": {"type": "excerpt"}, "excerpt_of": excerpt_of | {"spans": []}},
    ):
        assert (await client.post(f"/v2/texts/{part}/editions", json=body)).status_code == 422

    for edition_id, ranges, status in (
        (source, [(0, 5), (4, 8)], 422),  # overlapping
        (source, [(3, 3)], 422),  # empty
        (source, [(0, 100)], 422),  # beyond the source content
        (other, [(0, 5)], 422),  # not an edition of the whole text
        (edition, [(0, 2)], 422),  # the part's own edition, which is not of the whole text either
        (generate_id(), [(0, 2)], 404),
    ):
        assert (await _excerpt(client, part, edition_id, *ranges)).status_code == status
    for edition_id in (source, edition):  # an edition of the same text, directly or through an excerpt edition
        assert (await _excerpt(client, whole, edition_id, (0, 2))).status_code == 422
    assert (await client.get(f"/v2/texts/{whole}")).json()["parts"] == [part]
