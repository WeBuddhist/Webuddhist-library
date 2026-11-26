import asyncio
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from pydantic import ValidationError

from database import api_key, edition
from database import application as application_database
from database import category as category_database
from database import segment as segment_database
from database import tag as tag_database
from database import text as text_database
from errors import DataConflictError, DataNotFoundError
from identifier import generate_id
from models.annotation import SegmentationInput
from models.category import CategoryInput
from models.edition import EditionInput
from models.tag import TagInput
from models.text import TextInput
from tests.helpers import get_all_segments_by_edition

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest_asyncio.fixture(loop_scope="session")
async def work(test_database):
    db = test_database
    await application_database.create(db, "other", "Other")
    a = await tag_database.create(db, TagInput(title={"en": "A"}), "test_application")
    b = await tag_database.create(db, TagInput(title={"en": "B"}), "other")
    category = await category_database.create(db, CategoryInput(title={"en": "Another category"}), "test_application")
    foreign_category = await category_database.create(db, CategoryInput(title={"en": "Foreign category"}), "other")
    text = await text_database.create(
        db, TextInput(title={"bo": "Source"}, language="bo", category_id="category", tag_ids=[a, b])
    )
    _, key = await api_key.create(db, generate_id(), "Bound", "test@example.com", "test_application")
    return {
        "text": text,
        "a": a,
        "b": b,
        "category": category,
        "foreign_category": foreign_category,
        "headers": {"X-API-Key": key, "X-Application": "test_application"},
    }


@pytest.mark.parametrize(
    ("category", "error"),
    [(None, "valid string"), ("category", "must not be supplied"), ("different", "must not be supplied")],
)
async def test_translation_rejects_any_category(category, error):
    with pytest.raises(ValidationError, match=error):
        TextInput(title={"en": "Translation"}, language="en", translation_of="source", category_id=category)


@pytest.mark.parametrize("relation", [{}, {"commentary_of": "source"}])
async def test_new_work_requires_category(relation):
    with pytest.raises(ValidationError, match="category_id is required"):
        TextInput(title={"en": "Text"}, language="en", **relation)


async def test_tagged_translation_inherits_category(auth_client, test_database, work):
    new_tag = await tag_database.create(test_database, TagInput(title={"en": "Translation tag"}), "test_application")
    response = await auth_client.post(
        "/v2/texts",
        headers=work["headers"],
        json={
            "title": {"en": "Translation"},
            "language": "en",
            "translation_of": work["text"],
            "tag_ids": [new_tag],
        },
    )
    assert response.status_code == 201, response.text
    translated = await text_database.get(test_database, response.json()["id"])
    assert translated.category_id == "category"
    assert set(translated.tag_ids) == {work["a"], work["b"], new_tag}
    assert set((await text_database.get(test_database, work["text"])).tag_ids) == set(translated.tag_ids)
    assert await text_database.get_work_id(test_database, translated.id) == await text_database.get_work_id(test_database, work["text"])


@pytest.mark.parametrize("application", [None, "test_application"])
async def test_missing_tag_identifies_tag(client, work, application):
    headers = {"X-Application": application} if application else {}
    response = await client.post(f"/v2/texts/{work['text']}/tags/missing-tag", headers=headers)
    scope = f" in application '{application}'" if application else ""
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == f"Tag 'missing-tag' not found{scope}"


@pytest.mark.parametrize(("owner", "attach", "detach"), [
    ("Work", tag_database.tag_work, tag_database.untag_work),
    ("Segment", tag_database.tag_segment, tag_database.untag_segment),
])
async def test_missing_tag_owner_keeps_lock_order_and_errors(test_database, work, owner, attach, detach, monkeypatch):
    locks = AsyncMock(wraps=tag_database.lock_nodes)
    monkeypatch.setattr(tag_database, "lock_nodes", locks)
    tag_id = work["a"]
    for operation, message in (
        (attach, f"{owner} 'missing' not found"),
        (detach, f"Tag '{tag_id}' is not attached to {owner} 'missing'"),
    ):
        locks.reset_mock()
        with pytest.raises(DataNotFoundError) as error:
            await operation(test_database, "missing", tag_id)
        assert str(error.value) == message
        assert [call.args[1:] for call in locks.await_args_list] == [(owner, ["missing"]), ("Tag", [tag_id])]


async def test_bound_key_cannot_create_text_with_foreign_category(auth_client, test_database, work):
    before = {text.id for text in await text_database.get_all(test_database, offset=0, limit=100, filters=None)}
    response = await auth_client.post(
        "/v2/texts",
        headers=work["headers"],
        json={
            "title": {"en": "Unauthorized"},
            "language": "en",
            "category_id": work["foreign_category"],
        },
    )
    assert response.status_code == 403, response.text
    assert {text.id for text in await text_database.get_all(test_database, offset=0, limit=100, filters=None)} == before


async def test_tag_replacement_preserves_other_application(auth_client, test_database, work):
    response = await auth_client.patch(f"/v2/texts/{work['text']}", headers=work["headers"], json={"tag_ids": []})
    assert response.status_code == 200, response.text
    assert (await text_database.get(test_database, work["text"])).tag_ids == [work["b"]]
    response = await auth_client.patch(
        f"/v2/texts/{work['text']}", headers=work["headers"], json={"tag_ids": [work["b"]]}
    )
    assert response.status_code == 403, response.text
    assert (await text_database.get(test_database, work["text"])).tag_ids == [work["b"]]


@pytest.mark.parametrize("method", ["POST", "DELETE"])
async def test_foreign_tag_cannot_be_attached_or_removed(auth_client, test_database, work, method):
    edition_id = generate_id()
    await edition.create(
        test_database,
        EditionInput(type="critical"),
        edition_id,
        work["text"],
        content_length=10,
        segmentation=SegmentationInput(segments=[{"lines": [{"start": 0, "end": 10}]}]),
    )
    segment = (await get_all_segments_by_edition(test_database, edition_id))[0]
    await tag_database.tag_segment(test_database, segment.id, work["b"])
    work_id = await text_database.get_work_id(test_database, work["text"])
    for resource, resource_id, owner, owner_id in (
        ("texts", work["text"], "Work", work_id), ("segments", segment.id, "Segment", segment.id)
    ):
        response = await auth_client.request(
            method, f"/v2/{resource}/{resource_id}/tags/{work['b']}", headers=work["headers"]
        )
        assert response.status_code == 404, response.text
        if method == "POST":
            assert response.json()["detail"] == f"Tag '{work['b']}' not found in application 'test_application'"
        else:
            assert response.json()["detail"] == f"Tag '{work['b']}' is not attached to {owner} '{owner_id}'"
    assert work["b"] in (await text_database.get(test_database, work["text"])).tag_ids
    assert work["b"] in (await segment_database.get(test_database, segment.id)).tag_ids


async def test_category_change_checks_owner_and_expected_value(auth_client, test_database, work):
    url = f"/v2/texts/{work['text']}"
    for body, status in (
        ({"category_id": "category"}, 200),
        ({"category_id": work["category"]}, 409),
        ({"category_id": work["foreign_category"], "expected_category_id": "category"}, 403),
        ({"category_id": work["category"], "expected_category_id": "category"}, 200),
        ({"category_id": "category", "expected_category_id": "category"}, 409),
    ):
        response = await auth_client.patch(url, headers=work["headers"], json=body)
        assert response.status_code == status, response.text
    assert (await text_database.get(test_database, work["text"])).category_id == work["category"]


async def test_cannot_replace_another_applications_existing_category(auth_client, test_database, work):
    foreign = await text_database.create(
        test_database, TextInput(title={"en": "Foreign work"}, language="en", category_id=work["foreign_category"])
    )
    response = await auth_client.patch(
        f"/v2/texts/{foreign}",
        headers=work["headers"],
        json={
            "category_id": "category",
            "expected_category_id": work["foreign_category"],
        },
    )
    assert response.status_code == 403, response.text
    assert (await text_database.get(test_database, foreign)).category_id == work["foreign_category"]


async def test_category_edits_through_translations_share_one_lock(auth_client, test_database, work):
    translation = await text_database.create(
        test_database, TextInput(title={"en": "Translation"}, language="en", translation_of=work["text"])
    )
    other_category = await category_database.create(
        test_database, CategoryInput(title={"en": "Third category"}), "test_application"
    )
    responses = await asyncio.gather(
        *(
            auth_client.patch(
                f"/v2/texts/{text_id}",
                headers=work["headers"],
                json={
                    "category_id": category_id,
                    "expected_category_id": "category",
                },
            )
            for text_id, category_id in ((work["text"], work["category"]), (translation, other_category))
        )
    )
    assert sorted(r.status_code for r in responses) == [200, 409], [r.text for r in responses]
    assert (await text_database.get(test_database, translation)).category_id == (
        await text_database.get(test_database, work["text"])
    ).category_id


async def test_bound_keys_cannot_administer_applications(auth_client, test_database, work):
    response = await auth_client.post("/v2/applications", headers=work["headers"], json={"name": "new-app"})
    assert response.status_code == 403, response.text
    await application_database.create(test_database, "empty-app", "Empty")
    response = await auth_client.delete("/v2/applications/empty-app", headers=work["headers"])
    assert response.status_code == 403, response.text
    assert await application_database.exists(test_database, "empty-app")


async def test_taxonomy_preserves_full_language_tags(test_database):
    title = {"en": "Generic", "en-US": "American"}
    tag_id = await tag_database.create(test_database, TagInput(title=title, description=title), "test_application")
    tag = next(t for t in await tag_database.get_all(test_database, "test_application") if t.id == tag_id)
    category_id = await category_database.create(
        test_database, CategoryInput(title=title, description=title), "test_application"
    )
    category = await category_database.get(test_database, category_id, "test_application")
    for record in (tag, category):
        assert record.title.root == title
        assert record.description.root == title


async def test_deleting_shared_work_tag_updates_every_translation(test_database, work):
    db = test_database
    translation = await text_database.create(db, TextInput(title={"en": "Projection translation"}, language="en", translation_of=work["text"]))

    await tag_database.delete(db, work["a"], "test_application")
    for text_id in (work["text"], translation):
        assert work["a"] not in (await text_database.get(db, text_id)).tag_ids


async def test_tag_deletion_rejects_attachment_added_while_acquiring_locks(test_database, work, monkeypatch):
    db = test_database
    other = await text_database.create(
        db, TextInput(title={"en": "Concurrent tag"}, language="en", category_id="category")
    )
    other_work = await text_database.get_work_id(db, other)
    original_work = await text_database.get_work_id(db, work["text"])
    lock_nodes = tag_database.lock_nodes
    attached = False

    async def attach_before_tag_lock(tx, label, ids):
        nonlocal attached
        if label == "Tag" and not attached:
            attached = True
            await tag_database.tag_work(db, other_work, work["a"], "test_application")
        await lock_nodes(tx, label, ids)

    with monkeypatch.context() as patch:
        patch.setattr(tag_database, "lock_nodes", attach_before_tag_lock)
        with pytest.raises(DataConflictError, match="Tag attachments changed"):
            await tag_database.delete(db, work["a"], "test_application")

    for text_id in (work["text"], other):
        assert work["a"] in (await text_database.get(db, text_id)).tag_ids
    assert set(await tag_database.delete(db, work["a"], "test_application")) == {original_work, other_work}


@pytest.mark.parametrize('scope', ['test_application', None])
async def test_unbound_key_scope_controls_shared_work_tag_replacement(auth_client, test_database, work, scope):
    _, key = await api_key.create(test_database, generate_id(), 'Admin', 'admin@example.com')
    headers = {'X-API-Key': key}
    if scope is not None:
        headers['X-Application'] = scope
    response = await auth_client.patch(f"/v2/texts/{work['text']}", headers=headers, json={'tag_ids': []})
    assert response.status_code == 200, response.text
    assert (await text_database.get(test_database, work["text"])).tag_ids == ([work["b"]] if scope else [])
    foreign = await text_database.create(test_database, TextInput(title={'en': 'Foreign category admin'}, language='en', category_id=work['foreign_category']))
    response = await auth_client.patch(f'/v2/texts/{foreign}', headers=headers, json={'category_id': 'category', 'expected_category_id': work['foreign_category']})
    assert response.status_code == (403 if scope else 200), response.text
    assert (await text_database.get(test_database, foreign)).category_id == (work['foreign_category'] if scope else 'category')
