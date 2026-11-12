# pylint: disable=redefined-outer-name
"""
Integration tests for /v2/tag-groups and grouped tag filtering on /v2/texts.

Tags in the same group are ORed by the text filter; groups, and tags outside any group, are combined by
tag_id_match (AND for the default "all", OR for "any").
"""

import pytest

from identifier import generate_id

APP = {"X-Application": "test_application"}
APP_B = {"X-Application": "app_b"}

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _create(client, path, data, headers=APP):
    response = await client.post(path, json=data, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _tag(client, title, headers=APP):
    return await _create(client, "/v2/tags", {"title": {"en": title}}, headers)


async def _group(client, title, headers=APP):
    return await _create(client, "/v2/tag-groups", {"title": {"en": title}}, headers)


async def _add(client, group_id, tag_id, headers=APP):
    return await client.post(f"/v2/tag-groups/{group_id}/tags/{tag_id}", headers=headers)


async def _groups(client, headers=APP):
    response = await client.get("/v2/tag-groups", headers=headers)
    assert response.status_code == 200, response.text
    return {group["id"]: group for group in response.json()}


async def _tag_group_ids(client):
    response = await client.get("/v2/tags", headers=APP)
    assert response.status_code == 200, response.text
    return {tag["id"]: tag["group_id"] for tag in response.json()}


async def _seed_app_b(test_database):
    async with test_database.get_session() as session:
        await session.run("MERGE (:Application {id: 'app_b', name: 'Application B'})")


async def _text(client, tag_ids):
    return await _create(
        client,
        "/v2/texts",
        {
            "title": {"en": f"Grouped text {generate_id()}"},
            "language": "en",
            "category_id": "category",
            "tag_ids": tag_ids,
        },
    )


async def _list_ids(client, tag_ids, **params):
    response = await client.get("/v2/texts", params={"tag_id": ",".join(tag_ids), **params})
    assert response.status_code == 200, response.text
    return {text["id"] for text in response.json()["items"]}


class TestTagGroupEndpoints:
    async def test_create_and_list_group(self, client, test_database):
        group_id = await _create(
            client, "/v2/tag-groups", {"title": {"en": "Tradition"}, "description": {"en": "Buddhist traditions"}}
        )

        group = (await _groups(client))[group_id]
        assert group["title"] == {"en": "Tradition"}
        assert group["description"] == {"en": "Buddhist traditions"}
        assert group["tag_ids"] == []

    async def test_duplicate_title_rejected_within_application_only(self, client, test_database):
        await _seed_app_b(test_database)
        await _group(client, "Tradition")

        response = await client.post("/v2/tag-groups", json={"title": {"en": "tradition"}}, headers=APP)
        assert response.status_code == 422
        assert "already exists" in response.json()["error"]
        await _group(client, "Tradition", APP_B)

    async def test_requires_known_application(self, client, test_database):
        assert (await client.get("/v2/tag-groups")).status_code == 422
        response = await client.get("/v2/tag-groups", headers={"X-Application": "nonexistent_app"})
        assert response.status_code == 404

    async def test_patch_merges_localizations_per_language(self, client, test_database):
        group_id = await _create(
            client,
            "/v2/tag-groups",
            {"title": {"en": "Traditon", "bo": "ལུགས།"}, "description": {"en": "Old description"}},
        )
        tag_id = await _tag(client, "Member")
        await _add(client, group_id, tag_id)

        response = await client.patch(
            f"/v2/tag-groups/{group_id}", json={"title": {"en": "Tradition", "zh": "传统"}}, headers=APP
        )
        assert response.status_code == 200, response.text
        assert response.json() == {
            "id": group_id,
            "title": {"en": "Tradition", "bo": "ལུགས།", "zh": "传统"},
            "description": {"en": "Old description"},
            "tag_ids": [tag_id],
        }

        response = await client.patch(
            f"/v2/tag-groups/{group_id}", json={"description": {"bo": "ལུགས་སྲོལ།"}}, headers=APP
        )
        assert response.status_code == 200, response.text
        group = (await _groups(client))[group_id]
        assert group["title"] == {"en": "Tradition", "bo": "ལུགས།", "zh": "传统"}
        assert group["description"] == {"en": "Old description", "bo": "ལུགས་སྲོལ།"}
        assert group["tag_ids"] == [tag_id]

    async def test_patch_adds_description_to_group_without_one(self, client, test_database):
        group_id = await _group(client, "Tradition")

        response = await client.patch(f"/v2/tag-groups/{group_id}", json={"description": {"en": "New"}}, headers=APP)
        assert response.status_code == 200, response.text
        assert response.json()["description"] == {"en": "New"}

    async def test_language_codes_are_stored_in_bcp47_casing(self, client, test_database):
        group_id = await _create(
            client,
            "/v2/tag-groups",
            {"title": {"EN-us": "Tradition", "BO": "ལུགས།"}, "description": {"ZH-hans": "传统"}},
        )
        group = (await _groups(client))[group_id]
        assert group["title"] == {"en-US": "Tradition", "bo": "ལུགས།"}
        assert group["description"] == {"zh-Hans": "传统"}

        response = await client.patch(
            f"/v2/tag-groups/{group_id}", json={"title": {"en-us": "Traditions", "Zh": "传统"}}, headers=APP
        )
        assert response.status_code == 200, response.text
        assert response.json()["title"] == {"en-US": "Traditions", "bo": "ལུགས།", "zh": "传统"}

    @pytest.mark.parametrize("field", ["title", "description"])
    async def test_one_localization_per_language(self, client, test_database, field):
        body = {"title": {"en": "Tradition"}, field: {"en-US": "One", "en-us": "Two"}}
        response = await client.post("/v2/tag-groups", json=body, headers=APP)
        assert response.status_code == 422

        group_id = await _group(client, "Tradition")
        response = await client.patch(
            f"/v2/tag-groups/{group_id}", json={field: {"en": "One", "EN": "Two"}}, headers=APP
        )
        assert response.status_code == 422
        assert "more than one localization for language 'en'" in response.text
        assert (await _groups(client))[group_id]["title"] == {"en": "Tradition"}

    async def test_patch_title_must_stay_unique(self, client, test_database):
        group_id = await _group(client, "Tradition")
        await _group(client, "Format")

        response = await client.patch(f"/v2/tag-groups/{group_id}", json={"title": {"en": "format"}}, headers=APP)
        assert response.status_code == 422
        assert "already exists" in response.json()["error"]

        response = await client.patch(f"/v2/tag-groups/{group_id}", json={"title": {"en": "Tradition"}}, headers=APP)
        assert response.status_code == 200, response.text

    @pytest.mark.parametrize("body", [{}, {"title": None}, {"description": None}, {"title": {}}, {"tag_ids": []}])
    async def test_patch_rejects_invalid_body(self, client, test_database, body):
        group_id = await _group(client, "Tradition")

        response = await client.patch(f"/v2/tag-groups/{group_id}", json=body, headers=APP)
        assert response.status_code == 422

    async def test_patch_missing_or_foreign_group(self, client, test_database):
        await _seed_app_b(test_database)
        foreign = await _group(client, "Foreign", APP_B)

        for group_id in ("missing", foreign):
            response = await client.patch(f"/v2/tag-groups/{group_id}", json={"title": {"en": "X"}}, headers=APP)
            assert response.status_code == 404
        assert (await _groups(client, APP_B))[foreign]["title"] == {"en": "Foreign"}

    async def test_groups_are_scoped_to_application(self, client, test_database):
        await _seed_app_b(test_database)
        group_a = await _group(client, "App A group")
        group_b = await _group(client, "App B group", APP_B)

        assert set(await _groups(client)) == {group_a}
        assert set(await _groups(client, APP_B)) == {group_b}

    async def test_add_tag_shows_on_group_and_tag(self, client, test_database):
        group_id = await _group(client, "Tradition")
        tag_id = await _tag(client, "Theravada")

        assert (await _add(client, group_id, tag_id)).status_code == 204

        assert (await _groups(client))[group_id]["tag_ids"] == [tag_id]
        assert (await _tag_group_ids(client))[tag_id] == group_id

    async def test_adding_to_another_group_moves_the_tag(self, client, test_database):
        first, second = await _group(client, "First"), await _group(client, "Second")
        tag_id = await _tag(client, "Movable")

        await _add(client, first, tag_id)
        assert (await _add(client, second, tag_id)).status_code == 204
        assert (await _add(client, second, tag_id)).status_code == 204  # idempotent

        groups = await _groups(client)
        assert groups[first]["tag_ids"] == []
        assert groups[second]["tag_ids"] == [tag_id]

    async def test_add_rejects_missing_or_foreign_tag_and_group(self, client, test_database):
        await _seed_app_b(test_database)
        group_id = await _group(client, "Tradition")
        foreign_tag = await _tag(client, "Foreign tag", APP_B)
        foreign_group = await _group(client, "Foreign group", APP_B)
        tag_id = await _tag(client, "Local tag")

        for group, tag in (
            (group_id, foreign_tag),
            (group_id, "missing"),
            (foreign_group, tag_id),
            ("missing", tag_id),
        ):
            response = await _add(client, group, tag)
            assert response.status_code == 404, (group, tag, response.text)
        assert (await _groups(client))[group_id]["tag_ids"] == []

    async def test_remove_tag_from_group(self, client, test_database):
        group_id = await _group(client, "Tradition")
        tag_id = await _tag(client, "Removable")
        await _add(client, group_id, tag_id)

        response = await client.delete(f"/v2/tag-groups/{group_id}/tags/{tag_id}", headers=APP)
        assert response.status_code == 204
        assert (await _tag_group_ids(client))[tag_id] is None

        response = await client.delete(f"/v2/tag-groups/{group_id}/tags/{tag_id}", headers=APP)
        assert response.status_code == 404

    async def test_delete_group_requires_it_to_be_empty(self, client, test_database):
        group_id = await _group(client, "Tradition")
        tag_id = await _tag(client, "Member")
        await _add(client, group_id, tag_id)

        response = await client.delete(f"/v2/tag-groups/{group_id}", headers=APP)
        assert response.status_code == 409

        await client.delete(f"/v2/tag-groups/{group_id}/tags/{tag_id}", headers=APP)
        assert (await client.delete(f"/v2/tag-groups/{group_id}", headers=APP)).status_code == 204
        assert group_id not in await _groups(client)
        assert tag_id in await _tag_group_ids(client)

    async def test_delete_missing_group(self, client, test_database):
        response = await client.delete("/v2/tag-groups/missing", headers=APP)
        assert response.status_code == 404

    async def test_deleting_tag_leaves_group(self, client, test_database):
        group_id = await _group(client, "Tradition")
        tag_id = await _tag(client, "Deleted member")
        await _add(client, group_id, tag_id)

        assert (await client.delete(f"/v2/tags/{tag_id}", headers=APP)).status_code == 204
        assert (await _groups(client))[group_id]["tag_ids"] == []


@pytest.fixture
async def traditions(client, test_database):
    """Theravada and Mahayana in a Tradition group; Chant has no group."""
    theravada, mahayana, chant = [await _tag(client, name) for name in ("Theravada", "Mahayana", "Chant")]
    group_id = await _group(client, "Tradition")
    for tag in (theravada, mahayana):
        assert (await _add(client, group_id, tag)).status_code == 204
    texts = {
        "theravada_chant": await _text(client, [theravada, chant]),
        "mahayana_chant": await _text(client, [mahayana, chant]),
        "theravada": await _text(client, [theravada]),
        "mahayana": await _text(client, [mahayana]),
        "chant": await _text(client, [chant]),
        "untagged": await _text(client, []),
    }
    return {"theravada": theravada, "mahayana": mahayana, "chant": chant, "group": group_id, "texts": texts}


class TestGroupedTagFiltering:
    @pytest.mark.parametrize(
        ("selection", "expected"),
        [
            (["theravada"], {"theravada_chant", "theravada"}),
            (["theravada", "mahayana"], {"theravada_chant", "mahayana_chant", "theravada", "mahayana"}),
            (["chant"], {"theravada_chant", "mahayana_chant", "chant"}),
            (["mahayana", "chant"], {"mahayana_chant"}),
            (["theravada", "mahayana", "chant"], {"theravada_chant", "mahayana_chant"}),
        ],
    )
    async def test_or_within_group_and_across_groups(self, client, traditions, selection, expected):
        result = await _list_ids(client, [traditions[name] for name in selection])
        assert result == {traditions["texts"][name] for name in expected}

    async def test_any_ors_the_groups(self, client, traditions):
        selection = [traditions["theravada"], traditions["mahayana"], traditions["chant"]]
        result = await _list_ids(client, selection, tag_id_match="any")
        assert result == set(traditions["texts"].values()) - {traditions["texts"]["untagged"]}

    async def test_ungrouping_restores_and_matching(self, client, traditions):
        for tag in ("theravada", "mahayana"):
            await client.delete(f"/v2/tag-groups/{traditions['group']}/tags/{traditions[tag]}", headers=APP)

        assert await _list_ids(client, [traditions["theravada"], traditions["mahayana"]]) == set()

    async def test_unknown_tag_still_matches_nothing(self, client, traditions):
        assert await _list_ids(client, [traditions["theravada"], "missing-tag"]) == set()

    async def test_combines_with_other_filters(self, client, traditions):
        result = await _list_ids(
            client, [traditions["theravada"], traditions["mahayana"], traditions["chant"]], category_id="category"
        )
        assert result == {traditions["texts"]["theravada_chant"], traditions["texts"]["mahayana_chant"]}

    async def test_languages_facet_uses_groups(self, client, traditions):
        response = await client.get(
            "/v2/texts/languages",
            params={"tag_id": ",".join([traditions["theravada"], traditions["mahayana"], traditions["chant"]])},
        )
        assert response.status_code == 200, response.text
        assert {row["code"]: row["text_count"] for row in response.json()} == {"en": 2}
