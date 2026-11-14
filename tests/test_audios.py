# pylint: disable=redefined-outer-name
"""
Integration tests for audio endpoints using a real Neo4j test instance.

Tests endpoints:
- GET/POST /v2/texts/{text_id}/audios
- GET/PATCH/DELETE /v2/audios/{audio_id}
- DELETE /v2/texts/{text_id} while an audio recites it
"""

import asyncio

import pytest
from exceptions import DataConflictError, DataNotFoundError
from identifier import generate_id
from models.audio import AudioInput
from models.text import TextInput

from tests.test_recordings import RecordingHelpers


@pytest.mark.asyncio(loop_scope="session")
class TestAudiosEndpoints(RecordingHelpers):
    async def _text(self, db, title: str = "Heart Sutra", language: str = "bo", **links) -> str:
        if "translation_of" not in links:
            links["category_id"] = "category"  # Translations inherit their category.
        return await db.text.create(TextInput(title={language: f"{title} {generate_id()[:6]}"}, language=language, **links))

    async def _work(self, db, node_id: str, relationship: str) -> str:
        async with db.get_session() as session:
            result = await session.run(f"MATCH ({{id: $id}})-[:{relationship}]->(w:Work) RETURN w.id AS id", id=node_id)
            return (await result.single())["id"]

    async def test_create_audio(self, client, test_database):
        singer = await self._create_person(test_database, "Lama Tsering")
        musician = await self._create_person(test_database, "Drummer")
        text_id = await self._text(test_database)

        response = await client.post(
            f"/v2/texts/{text_id}/audios",
            json={
                "title": {"en": "Heart Sutra, morning chant"},
                "license": "cc-by",
                "date": "2024",
                "contributions": [
                    {"type": "person", "id": singer, "role": "singer"},
                    {"type": "person", "id": musician, "role": "musician"},
                ],
            },
        )

        assert response.status_code == 201, response.text
        audio_id = response.json()["id"]
        audio = (await client.get(f"/v2/audios/{audio_id}")).json()
        assert audio["id"] == audio_id
        assert audio["text_id"] == text_id
        assert audio["title"] == {"en": "Heart Sutra, morning chant"}
        assert audio["language"] == "bo"  # Taken from the recited text.
        assert audio["license"] == "cc-by"
        assert audio["date"] == "2024"
        assert audio["recordings"] == []
        assert sorted((c["role"], c["id"]) for c in audio["contributions"]) == sorted(
            [("singer", singer), ("musician", musician)]
        )
        assert await self._work(test_database, audio_id, "AUDIO_OF") == await self._work(
            test_database, text_id, "TEXT_OF"
        )

    async def test_create_audio_with_explicit_language_and_defaults(self, client, test_database):
        text_id = await self._text(test_database)

        response = await client.post(
            f"/v2/texts/{text_id}/audios",
            json={"language": "en", "contributions": [{"type": "ai", "id": "tts", "role": "narrator"}]},
        )

        assert response.status_code == 201, response.text
        audio = (await client.get(f"/v2/audios/{response.json()['id']}")).json()
        assert audio["language"] == "en"
        assert audio["license"] == "public"
        assert "title" not in audio

    async def test_audio_of_a_translation_belongs_to_the_source_work(self, client, test_database):
        source = await self._text(test_database)
        translation = await self._text(test_database, "Heart Sutra translation", "en", translation_of=source)

        response = await client.post(
            f"/v2/texts/{translation}/audios",
            json={"contributions": [{"type": "ai", "id": "tts", "role": "narrator"}]},
        )

        assert response.status_code == 201, response.text
        audio_id = response.json()["id"]
        assert (await client.get(f"/v2/audios/{audio_id}")).json()["language"] == "en"
        assert await self._work(test_database, audio_id, "AUDIO_OF") == await self._work(
            test_database, source, "TEXT_OF"
        )

    @pytest.mark.parametrize("role", ["author", "translator", "reviser", "scholar"])
    async def test_create_audio_rejects_non_performer_role(self, client, test_database, role):
        person_id = await self._create_person(test_database)
        text_id = await self._text(test_database)

        response = await client.post(
            f"/v2/texts/{text_id}/audios",
            json={"contributions": [{"type": "person", "id": person_id, "role": role}]},
        )

        assert response.status_code == 422
        assert "narrator, singer, musician" in response.text

    async def test_create_audio_requires_a_contribution(self, client, test_database):
        text_id = await self._text(test_database)

        response = await client.post(f"/v2/texts/{text_id}/audios", json={"contributions": []})

        assert response.status_code == 422

    async def test_create_audio_rejects_unknown_person(self, client, test_database):
        text_id = await self._text(test_database)

        response = await client.post(
            f"/v2/texts/{text_id}/audios",
            json={"contributions": [{"type": "person", "id": generate_id(), "role": "singer"}]},
        )

        assert response.status_code == 422

    async def test_create_audio_on_missing_text(self, client):
        response = await client.post(
            f"/v2/texts/{generate_id()}/audios",
            json={"contributions": [{"type": "ai", "id": "tts", "role": "narrator"}]},
        )

        assert response.status_code == 404

    async def test_list_audios_for_text(self, client, test_database):
        text_id = await self._text(test_database)
        person_id = await self._create_person(test_database)
        ids = sorted([await self._create_audio(client, text_id, person_id) for _ in range(2)])

        response = await client.get(f"/v2/texts/{text_id}/audios")

        assert response.status_code == 200
        assert [audio["id"] for audio in response.json()] == ids
        assert (await client.get(f"/v2/texts/{await self._text(test_database)}/audios")).json() == []

    async def test_list_audios_for_missing_text(self, client):
        assert (await client.get(f"/v2/texts/{generate_id()}/audios")).status_code == 404

    async def test_get_missing_audio(self, client):
        assert (await client.get(f"/v2/audios/{generate_id()}")).status_code == 404

    async def test_patch_audio(self, client, test_database):
        text_id = await self._text(test_database)
        audio_id = await self._create_audio(client, text_id, await self._create_person(test_database))
        narrator = await self._create_person(test_database, "Reader")

        response = await client.patch(
            f"/v2/audios/{audio_id}",
            json={
                "title": {"en": "Evening chant"},
                "language": "en",
                "license": "cc0",
                "date": "2025",
                "contributions": [{"type": "person", "id": narrator, "role": "narrator"}],
            },
        )

        assert response.status_code == 200, response.text
        audio = response.json()
        assert audio["title"] == {"en": "Evening chant"}
        assert audio["language"] == "en"
        assert audio["license"] == "cc0"
        assert audio["date"] == "2025"
        assert [(c["role"], c["id"]) for c in audio["contributions"]] == [("narrator", narrator)]
        assert audio["text_id"] == text_id

    async def test_patch_rejects_non_performer_role(self, client, test_database):
        text_id = await self._text(test_database)
        person_id = await self._create_person(test_database)
        audio_id = await self._create_audio(client, text_id, person_id)

        response = await client.patch(
            f"/v2/audios/{audio_id}",
            json={"contributions": [{"type": "person", "id": person_id, "role": "author"}]},
        )

        assert response.status_code == 422

    async def test_patch_missing_audio(self, client):
        assert (await client.patch(f"/v2/audios/{generate_id()}", json={"date": "2024"})).status_code == 404

    async def test_delete_audio_keeps_person_and_text(self, client, test_database):
        text_id = await self._text(test_database)
        person_id = await self._create_person(test_database)
        audio_id = await self._create_audio(client, text_id, person_id)

        assert (await client.delete(f"/v2/audios/{audio_id}")).status_code == 204

        assert (await client.get(f"/v2/audios/{audio_id}")).status_code == 404
        assert (await client.get(f"/v2/persons/{person_id}")).status_code == 200
        assert (await client.get(f"/v2/texts/{text_id}")).status_code == 200
        assert await self._count(test_database, "Contribution") == 0

    async def test_delete_audio_with_recordings_is_rejected(self, client, test_database):
        text_id = await self._text(test_database)
        audio_id = await self._create_audio(client, text_id, await self._create_person(test_database))
        await self._create_recording(client, audio_id)

        response = await client.delete(f"/v2/audios/{audio_id}")

        assert response.status_code == 409
        assert "1 recording(s)" in response.text

    async def test_delete_missing_audio(self, client):
        assert (await client.delete(f"/v2/audios/{generate_id()}")).status_code == 404

    async def test_delete_text_with_audio_is_rejected(self, client, test_database):
        text_id = await self._text(test_database)
        await self._create_audio(client, text_id, await self._create_person(test_database))

        response = await client.delete(f"/v2/texts/{text_id}")

        assert response.status_code == 409
        assert "1 audio(s)" in response.text

    async def test_create_and_patch_reject_unknown_language(self, client, test_database):
        text_id = await self._text(test_database)
        body = {"language": "xx", "contributions": [{"type": "ai", "id": "tts", "role": "narrator"}]}

        assert (await client.post(f"/v2/texts/{text_id}/audios", json=body)).status_code == 400
        audio_id = await self._create_audio(client, text_id, await self._create_person(test_database))
        assert (await client.patch(f"/v2/audios/{audio_id}", json={"language": "xx"})).status_code == 400

    async def test_title_changes_and_delete_leave_no_orphan_titles(self, client, test_database):
        text_id = await self._text(test_database)
        person_id = await self._create_person(test_database)
        before = (await self._count(test_database, "Nomen"), await self._count(test_database, "LocalizedText"))
        response = await client.post(
            f"/v2/texts/{text_id}/audios",
            json={"title": {"en": "First"}, "contributions": [{"type": "person", "id": person_id, "role": "singer"}]},
        )
        audio_id = response.json()["id"]

        for title in ("Second", "Third"):
            assert (await client.patch(f"/v2/audios/{audio_id}", json={"title": {"en": title}})).status_code == 200
        assert (await self._count(test_database, "Nomen"), await self._count(test_database, "LocalizedText")) == (
            before[0] + 1,
            before[1] + 1,
        )

        assert (await client.delete(f"/v2/audios/{audio_id}")).status_code == 204
        assert (await self._count(test_database, "Nomen"), await self._count(test_database, "LocalizedText")) == before

    @pytest.mark.parametrize("round_", range(3))
    async def test_audio_create_and_text_delete_serialize(self, test_database, round_):
        text_id = await self._text(test_database)
        audio = AudioInput(contributions=[{"type": "ai", "id": "tts", "role": "narrator"}])

        created, deleted = await asyncio.gather(
            test_database.audio.create(text_id, audio), test_database.text.delete(text_id), return_exceptions=True
        )

        # Either the audio exists and protects its text, or the text is gone and the audio was refused.
        if isinstance(created, str):
            assert isinstance(deleted, DataConflictError), deleted
            assert (await test_database.audio.get(created)).text_id == text_id
        else:
            assert isinstance(created, DataNotFoundError), created
            assert deleted is None
            assert await self._count(test_database, "Audio") == 0
