# pylint: disable=redefined-outer-name
"""
Integration tests for recording endpoints using a real Neo4j test instance.

Tests endpoints:
- GET/POST /v2/audios/{audio_id}/recordings
- GET /v2/editions/{edition_id}/recordings
- GET /v2/recordings/{recording_id}
- GET /v2/recordings/{recording_id}/audio
- PATCH /v2/recordings/{recording_id}
- DELETE /v2/recordings/{recording_id}
- PUT /v2/recordings/{recording_id}/follows/{edition_id}
- DELETE /v2/recordings/{recording_id}/follows
- GET/PUT/DELETE /v2/recordings/{recording_id}/cues
"""

import asyncio
import json
import logging

import pytest
from database.content_state import read_state
from exceptions import DataConflictError, DataNotFoundError, DataValidationError
from identifier import generate_id
from models.base import LocalizedString
from models.contribution import PersonContributionInput
from models.edition import EditionType
from models.enums import AudioFormat, ContributorRole
from models.person import PersonInput
from models.recording import RecordingCue, RecordingInput
from models.text import TextInput
from neo4j.exceptions import ClientError

logger = logging.getLogger(__name__)

AUDIO_BYTES = b"ID3\x04\x00\x00\x00\x00\x00\x00fake mp3 payload"


def _cue(start: int, end: int, start_ms: int, end_ms: int) -> dict:
    return {"span": {"start": start, "end": end}, "start_ms": start_ms, "end_ms": end_ms}


class RecordingHelpers:
    """Setup helpers shared by the recording test classes."""

    async def _create_person(self, db, name: str = "Test Singer") -> str:
        return await db.person.create(
            PersonInput(
                name=LocalizedString({"en": name, "bo": "གསུང་མཁན།"}),
                bdrc=f"P{generate_id()[:8]}",
            )
        )

    async def _create_text(self, db, person_id: str) -> str:
        return await db.text.create(
            TextInput(
                category_id="category",
                title=LocalizedString({"en": f"Recording host text {generate_id()[:6]}", "bo": f"བརྟག་དཔྱད། {generate_id()[:6]}"}),
                language="bo",
                contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.AUTHOR)],
            )
        )

    async def _create_edition(self, client, text_id: str, content: str = "Sample text content") -> str:
        response = await client.post(
            f"/v2/texts/{text_id}/editions",
            json={
                "content": content,
                "metadata": {
                    "type": EditionType.DIPLOMATIC.value,
                    "bdrc": f"W{generate_id()[:8]}",
                    "source": "Test Source",
                },
                "pagination": {
                    "volumes": [{"pages": [{"reference": "1a", "lines": [{"start": 0, "end": len(content)}]}]}]
                },
            },
        )
        assert response.status_code == 201, f"Failed to create edition: {response.json()}"
        return response.json()["id"]

    async def _create_audio(self, client, text_id: str, person_id: str) -> str:
        response = await client.post(
            f"/v2/texts/{text_id}/audios",
            json={"contributions": [{"type": "person", "id": person_id, "role": "singer"}]},
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    async def _setup_audio(self, client, test_database, content: str = "Sample text content") -> tuple[str, str]:
        """Create a text with one edition and an audio reciting it, returning the edition and audio IDs."""
        person_id = await self._create_person(test_database)
        text_id = await self._create_text(test_database, person_id)
        edition_id = await self._create_edition(client, text_id, content)
        return edition_id, await self._create_audio(client, text_id, person_id)

    @staticmethod
    def _upload(metadata: dict, content_type: str = "audio/mpeg", audio: bytes = AUDIO_BYTES) -> dict:
        return {
            "data": {"metadata": json.dumps(metadata)},
            "files": {"audio": ("reading.mp3", audio, content_type)},
        }

    async def _post_recording(self, client, audio_id: str, metadata: dict, **kwargs):
        return await client.post(f"/v2/audios/{audio_id}/recordings", **self._upload(metadata, **kwargs))

    async def _create_recording(self, client, audio_id: str, metadata: dict | None = None) -> str:
        response = await self._post_recording(client, audio_id, metadata or {})
        assert response.status_code == 201, response.text
        return response.json()["id"]

    async def _count(self, test_database, label: str) -> int:
        async with test_database.get_session() as session:
            result = await session.run(f"MATCH (n:{label}) RETURN count(n) AS count")
            return (await result.single())["count"]


@pytest.mark.asyncio(loop_scope="session")
class TestRecordingsEndpoints(RecordingHelpers):
    """Integration tests for recordings under an audio."""

    async def test_create_recording_that_follows_an_edition(self, client, test_database, mock_storage):
        edition_id, audio_id = await self._setup_audio(client, test_database)

        response = await self._post_recording(
            client,
            audio_id,
            {"title": {"en": "Without music"}, "duration_ms": 754000, "edition_id": edition_id},
        )

        assert response.status_code == 201, response.json()
        recording_id = response.json()["id"]
        recording = (await client.get(f"/v2/recordings/{recording_id}")).json()
        text_id = (await client.get(f"/v2/audios/{audio_id}")).json()["text_id"]
        assert recording == {
            "id": recording_id,
            "audio_id": audio_id,
            "text_id": text_id,
            "edition_id": edition_id,
            "title": {"en": "Without music"},
            "duration_ms": 754000,
            "format": "mp3",
            "size_bytes": len(AUDIO_BYTES),
        }
        key = f"objects/{recording_id}"
        assert await test_database.recording.get_storage_location(recording_id) == key
        assert mock_storage._storage[key] == AUDIO_BYTES

    async def test_create_recording_without_an_edition(self, client, test_database):
        _, audio_id = await self._setup_audio(client, test_database)

        recording_id = await self._create_recording(client, audio_id)

        recording = (await client.get(f"/v2/recordings/{recording_id}")).json()
        assert recording["audio_id"] == audio_id
        assert "edition_id" not in recording
        assert "title" not in recording

    async def test_create_recording_rejects_an_edition_of_another_text(self, client, test_database, mock_storage):
        _, audio_id = await self._setup_audio(client, test_database)
        person_id = await self._create_person(test_database, "Other Author")
        other_edition = await self._create_edition(client, await self._create_text(test_database, person_id))
        stored = len(mock_storage._storage)

        response = await self._post_recording(client, audio_id, {"edition_id": other_edition})

        assert response.status_code == 422
        assert "recites text" in response.text
        assert len(mock_storage._storage) == stored  # Rejected before the upload.

    async def test_create_recording_on_missing_audio(self, client):
        response = await self._post_recording(client, generate_id(), {})

        assert response.status_code == 404

    async def test_create_recording_on_missing_edition(self, client, test_database):
        _, audio_id = await self._setup_audio(client, test_database)

        response = await self._post_recording(client, audio_id, {"edition_id": generate_id()})

        assert response.status_code == 404

    async def test_create_recording_rejects_unsupported_content_type(self, client, test_database):
        _, audio_id = await self._setup_audio(client, test_database)

        response = await self._post_recording(client, audio_id, {}, content_type="text/plain")

        assert response.status_code == 422

    async def test_create_recording_rejects_empty_audio(self, client, test_database):
        _, audio_id = await self._setup_audio(client, test_database)

        response = await self._post_recording(client, audio_id, {}, audio=b"")

        assert response.status_code == 422

    async def test_old_edition_upload_route_is_removed(self, client, test_database):
        edition_id, _ = await self._setup_audio(client, test_database)

        response = await client.post(f"/v2/editions/{edition_id}/recordings", **self._upload({}))

        assert response.status_code == 405

    async def test_list_recordings_for_audio(self, client, test_database):
        _, audio_id = await self._setup_audio(client, test_database)
        ids = sorted([await self._create_recording(client, audio_id) for _ in range(2)])

        response = await client.get(f"/v2/audios/{audio_id}/recordings")

        assert response.status_code == 200
        assert [r["id"] for r in response.json()] == ids
        assert (await client.get(f"/v2/audios/{audio_id}")).json()["recordings"] == ids

    async def test_list_recordings_for_missing_audio(self, client):
        assert (await client.get(f"/v2/audios/{generate_id()}/recordings")).status_code == 404

    async def test_list_edition_recordings_returns_only_followers(self, client, test_database):
        edition_id, audio_id = await self._setup_audio(client, test_database)
        follower = await self._create_recording(client, audio_id, {"edition_id": edition_id})
        await self._create_recording(client, audio_id)

        response = await client.get(f"/v2/editions/{edition_id}/recordings")

        assert response.status_code == 200
        assert [r["id"] for r in response.json()] == [follower]

    async def test_list_recordings_for_missing_edition(self, client):
        assert (await client.get(f"/v2/editions/{generate_id()}/recordings")).status_code == 404

    async def test_get_missing_recording(self, client):
        assert (await client.get(f"/v2/recordings/{generate_id()}")).status_code == 404

    async def test_get_recording_audio_redirects(self, client, test_database):
        _, audio_id = await self._setup_audio(client, test_database)
        recording_id = await self._create_recording(client, audio_id)

        response = await client.get(f"/v2/recordings/{recording_id}/audio", follow_redirects=False)

        assert response.status_code == 307
        assert f"objects/{recording_id}" in response.headers["location"]

    async def test_get_audio_for_missing_recording(self, client):
        response = await client.get(f"/v2/recordings/{generate_id()}/audio", follow_redirects=False)

        assert response.status_code == 404

    async def test_patch_recording(self, client, test_database):
        _, audio_id = await self._setup_audio(client, test_database)
        recording_id = await self._create_recording(client, audio_id, {"title": {"en": "With music"}})

        response = await client.patch(
            f"/v2/recordings/{recording_id}",
            json={"title": {"en": "With tingsha and drum"}, "duration_ms": 761000},
        )

        assert response.status_code == 200, response.json()
        assert response.json()["title"] == {"en": "With tingsha and drum"}
        assert response.json()["duration_ms"] == 761000

    async def test_patch_missing_recording(self, client):
        response = await client.patch(f"/v2/recordings/{generate_id()}", json={"duration_ms": 1})

        assert response.status_code == 404

    async def test_delete_recording_keeps_audio_and_file(self, client, test_database, mock_storage):
        _, audio_id = await self._setup_audio(client, test_database)
        recording_id = await self._create_recording(client, audio_id, {"title": {"en": "With music"}})

        assert (await client.delete(f"/v2/recordings/{recording_id}")).status_code == 204

        assert (await client.get(f"/v2/recordings/{recording_id}")).status_code == 404
        assert (await client.get(f"/v2/audios/{audio_id}")).json()["recordings"] == []
        assert f"objects/{recording_id}" in mock_storage._storage

    async def test_delete_missing_recording(self, client):
        assert (await client.delete(f"/v2/recordings/{generate_id()}")).status_code == 404


@pytest.mark.asyncio(loop_scope="session")
class TestRecordingFollows(RecordingHelpers):
    """Integration tests for the edition a recording follows."""

    CONTENT = "0123456789"

    async def _put_cues(self, client, recording_id: str, cues: list[dict]):
        return await client.put(f"/v2/recordings/{recording_id}/cues", json=cues)

    async def _cues(self, client, recording_id: str) -> list[dict]:
        response = await client.get(f"/v2/recordings/{recording_id}/cues")
        assert response.status_code == 200
        return response.json()

    async def test_follow_an_edition(self, client, test_database):
        edition_id, audio_id = await self._setup_audio(client, test_database, self.CONTENT)
        recording_id = await self._create_recording(client, audio_id)

        response = await client.put(f"/v2/recordings/{recording_id}/follows/{edition_id}")

        assert response.status_code == 204
        assert (await client.get(f"/v2/recordings/{recording_id}")).json()["edition_id"] == edition_id

    async def test_follow_rejects_an_edition_of_another_text(self, client, test_database):
        _, audio_id = await self._setup_audio(client, test_database, self.CONTENT)
        recording_id = await self._create_recording(client, audio_id)
        person_id = await self._create_person(test_database, "Other Author")
        other_edition = await self._create_edition(client, await self._create_text(test_database, person_id))

        response = await client.put(f"/v2/recordings/{recording_id}/follows/{other_edition}")

        assert response.status_code == 422
        assert "recites text" in response.text

    async def test_follow_missing_edition_or_recording(self, client, test_database):
        edition_id, audio_id = await self._setup_audio(client, test_database, self.CONTENT)
        recording_id = await self._create_recording(client, audio_id)

        assert (await client.put(f"/v2/recordings/{recording_id}/follows/{generate_id()}")).status_code == 404
        assert (await client.put(f"/v2/recordings/{generate_id()}/follows/{edition_id}")).status_code == 404

    async def test_following_the_same_edition_keeps_cues(self, client, test_database):
        edition_id, audio_id = await self._setup_audio(client, test_database, self.CONTENT)
        recording_id = await self._create_recording(client, audio_id, {"edition_id": edition_id})
        await self._put_cues(client, recording_id, [_cue(0, 4, 0, 1200)])

        assert (await client.put(f"/v2/recordings/{recording_id}/follows/{edition_id}")).status_code == 204

        assert await self._cues(client, recording_id) == [_cue(0, 4, 0, 1200)]

    async def test_following_another_edition_deletes_cues(self, client, test_database):
        edition_id, audio_id = await self._setup_audio(client, test_database, self.CONTENT)
        text_id = (await client.get(f"/v2/audios/{audio_id}")).json()["text_id"]
        second_edition = await self._create_edition(client, text_id, self.CONTENT)
        recording_id = await self._create_recording(client, audio_id, {"edition_id": edition_id})
        await self._put_cues(client, recording_id, [_cue(0, 4, 0, 1200)])

        assert (await client.put(f"/v2/recordings/{recording_id}/follows/{second_edition}")).status_code == 204

        assert (await client.get(f"/v2/recordings/{recording_id}")).json()["edition_id"] == second_edition
        assert await self._cues(client, recording_id) == []
        assert await self._count(test_database, "Cue") == 0

    async def test_stop_following_deletes_cues_and_keeps_the_recording(self, client, test_database):
        edition_id, audio_id = await self._setup_audio(client, test_database, self.CONTENT)
        recording_id = await self._create_recording(client, audio_id, {"edition_id": edition_id})
        await self._put_cues(client, recording_id, [_cue(0, 4, 0, 1200)])

        assert (await client.delete(f"/v2/recordings/{recording_id}/follows")).status_code == 204

        recording = (await client.get(f"/v2/recordings/{recording_id}")).json()
        assert "edition_id" not in recording
        assert await self._count(test_database, "Cue") == 0
        assert (await client.delete(f"/v2/recordings/{recording_id}/follows")).status_code == 204

    async def test_database_rejects_moving_cues_to_another_edition(self, client, test_database):
        edition_id, audio_id = await self._setup_audio(client, test_database, self.CONTENT)
        text_id = (await client.get(f"/v2/audios/{audio_id}")).json()["text_id"]
        second_edition = await self._create_edition(client, text_id, self.CONTENT)
        recording_id = await self._create_recording(client, audio_id, {"edition_id": edition_id})
        await self._put_cues(client, recording_id, [_cue(0, 4, 0, 1200)])

        async with test_database.get_session() as session:
            with pytest.raises(ClientError, match="enforce_cue_requires_follows"):
                await (
                    await session.run(
                        """
                        MATCH (r:Recording {id: $recording_id})-[follows:FOLLOWS]->()
                        MATCH (m:Edition {id: $edition_id})
                        DELETE follows
                        CREATE (r)-[:FOLLOWS]->(m)
                        """,
                        recording_id=recording_id,
                        edition_id=second_edition,
                    )
                ).consume()
        assert await self._cues(client, recording_id) == [_cue(0, 4, 0, 1200)]


@pytest.mark.asyncio(loop_scope="session")
class TestRecordingCues(RecordingHelpers):
    """Integration tests for the cues of a recording."""

    CONTENT = "0123456789"

    async def _setup_recording(self, client, test_database) -> tuple[str, str]:
        """Create a recording that follows an edition whose content is CONTENT, returning both IDs."""
        edition_id, audio_id = await self._setup_audio(client, test_database, self.CONTENT)
        return edition_id, await self._create_recording(client, audio_id, {"edition_id": edition_id})

    async def _put_cues(self, client, recording_id: str, cues: list[dict]):
        return await client.put(f"/v2/recordings/{recording_id}/cues", json=cues)

    async def _get_cues(self, client, recording_id: str) -> list[dict]:
        response = await client.get(f"/v2/recordings/{recording_id}/cues")
        assert response.status_code == 200
        return response.json()

    async def test_put_and_get_cues(self, client, test_database):
        _, recording_id = await self._setup_recording(client, test_database)
        cues = [_cue(0, 4, 0, 1200), _cue(4, 10, 1200, 3000)]

        assert (await self._put_cues(client, recording_id, cues)).status_code == 204

        assert await self._get_cues(client, recording_id) == cues

    async def test_get_cues_empty(self, client, test_database):
        _, recording_id = await self._setup_recording(client, test_database)

        assert await self._get_cues(client, recording_id) == []

    async def test_cues_need_an_edition(self, client, test_database):
        _, audio_id = await self._setup_audio(client, test_database, self.CONTENT)
        recording_id = await self._create_recording(client, audio_id)

        response = await self._put_cues(client, recording_id, [_cue(0, 4, 0, 1200)])

        assert response.status_code == 422
        assert "does not follow an edition" in response.text
        assert await self._get_cues(client, recording_id) == []
        assert (await client.delete(f"/v2/recordings/{recording_id}/cues")).status_code == 204

    async def test_put_replaces_existing_cues(self, client, test_database):
        _, recording_id = await self._setup_recording(client, test_database)
        await self._put_cues(client, recording_id, [_cue(0, 4, 0, 1200), _cue(4, 10, 1200, 3000)])

        replacement = [_cue(2, 6, 500, 900)]
        assert (await self._put_cues(client, recording_id, replacement)).status_code == 204

        assert await self._get_cues(client, recording_id) == replacement

    async def test_put_accepts_repeated_span(self, client, test_database):
        _, recording_id = await self._setup_recording(client, test_database)
        refrain = [_cue(0, 4, 0, 1000), _cue(0, 4, 1000, 2000)]

        assert (await self._put_cues(client, recording_id, refrain)).status_code == 204

        assert await self._get_cues(client, recording_id) == refrain

    async def test_delete_cues(self, client, test_database):
        _, recording_id = await self._setup_recording(client, test_database)
        await self._put_cues(client, recording_id, [_cue(0, 4, 0, 1200)])
        spans_before = await self._count(test_database, "Span")

        response = await client.delete(f"/v2/recordings/{recording_id}/cues")

        assert response.status_code == 204
        assert await self._get_cues(client, recording_id) == []
        assert await self._count(test_database, "Cue") == 0
        assert await self._count(test_database, "Span") == spans_before - 1

    async def test_cues_on_missing_recording(self, client):
        recording_id = generate_id()

        assert (await client.get(f"/v2/recordings/{recording_id}/cues")).status_code == 404
        assert (await self._put_cues(client, recording_id, [_cue(0, 1, 0, 1)])).status_code == 404
        assert (await client.delete(f"/v2/recordings/{recording_id}/cues")).status_code == 404

    @pytest.mark.parametrize(
        ("cues", "message"),
        [
            ([_cue(0, 4, 900, 100)], "'start_ms' must be less than"),
            ([_cue(0, 4, 500, 500)], "'start_ms' must be less than"),
            ([_cue(3, 3, 0, 100)], "at least one character"),
            ([_cue(0, 4, 0, 1000), _cue(4, 8, 900, 2000)], "non-overlapping in time"),
            ([], "at least 1 item"),
        ],
    )
    async def test_put_rejects_invalid_cues(self, client, test_database, cues, message):
        _, recording_id = await self._setup_recording(client, test_database)

        response = await self._put_cues(client, recording_id, cues)

        assert response.status_code == 422
        assert message in response.text

    async def test_put_rejects_span_past_content(self, client, test_database):
        _, recording_id = await self._setup_recording(client, test_database)

        response = await self._put_cues(client, recording_id, [_cue(5, len(self.CONTENT) + 1, 0, 100)])

        assert response.status_code == 422
        assert await self._get_cues(client, recording_id) == []

    async def test_deleting_recording_removes_cues(self, client, test_database):
        _, recording_id = await self._setup_recording(client, test_database)
        await self._put_cues(client, recording_id, [_cue(0, 4, 0, 1200), _cue(4, 10, 1200, 3000)])
        spans_before = await self._count(test_database, "Span")

        assert (await client.delete(f"/v2/recordings/{recording_id}")).status_code == 204

        assert await self._count(test_database, "Cue") == 0
        assert await self._count(test_database, "Span") == spans_before - 2

    async def test_deleting_edition_with_cues_is_rejected(self, client, test_database):
        edition_id, recording_id = await self._setup_recording(client, test_database)
        await self._put_cues(client, recording_id, [_cue(0, 4, 0, 1200)])

        response = await client.delete(f"/v2/editions/{edition_id}")

        assert response.status_code == 409
        assert "1 recording(s) with cues follow it" in response.text
        assert await self._get_cues(client, recording_id) == [_cue(0, 4, 0, 1200)]

    async def test_deleting_edition_without_cues_keeps_the_recording(self, client, test_database):
        edition_id, recording_id = await self._setup_recording(client, test_database)

        assert (await client.delete(f"/v2/editions/{edition_id}")).status_code == 204

        recording = (await client.get(f"/v2/recordings/{recording_id}")).json()
        assert "edition_id" not in recording
        assert (await client.get(f"/v2/audios/{recording['audio_id']}")).status_code == 200

    async def _patch_content(self, client, edition_id: str, operation: dict) -> None:
        response = await client.patch(f"/v2/editions/{edition_id}/content", json=operation)
        assert response.status_code == 204, response.text

    async def test_insert_before_cue_shifts_it(self, client, test_database):
        edition_id, recording_id = await self._setup_recording(client, test_database)
        await self._put_cues(client, recording_id, [_cue(5, 8, 0, 1000)])

        await self._patch_content(client, edition_id, {"type": "insert", "position": 2, "text": "XX"})

        assert await self._get_cues(client, recording_id) == [_cue(7, 10, 0, 1000)]

    async def test_insert_inside_cue_expands_it(self, client, test_database):
        edition_id, recording_id = await self._setup_recording(client, test_database)
        await self._put_cues(client, recording_id, [_cue(3, 7, 0, 1000)])

        await self._patch_content(client, edition_id, {"type": "insert", "position": 5, "text": "XX"})

        assert await self._get_cues(client, recording_id) == [_cue(3, 9, 0, 1000)]

    async def test_delete_covering_cue_removes_only_that_cue(self, client, test_database):
        edition_id, recording_id = await self._setup_recording(client, test_database)
        await self._put_cues(client, recording_id, [_cue(0, 2, 0, 500), _cue(4, 6, 500, 1000)])

        await self._patch_content(client, edition_id, {"type": "delete", "start": 3, "end": 7})

        assert await self._get_cues(client, recording_id) == [_cue(0, 2, 0, 500)]
        assert await self._count(test_database, "Cue") == 1

    async def test_replace_exact_cue_removes_it(self, client, test_database):
        edition_id, recording_id = await self._setup_recording(client, test_database)
        await self._put_cues(client, recording_id, [_cue(3, 7, 0, 1000)])

        await self._patch_content(client, edition_id, {"type": "replace", "start": 3, "end": 7, "text": "XXXX"})

        assert await self._get_cues(client, recording_id) == []
        assert await self._count(test_database, "Cue") == 0


@pytest.mark.asyncio(loop_scope="session")
class TestRecordingConsistency(RecordingHelpers):
    """Edition revisions and concurrent writes around recordings, cues and edition deletes."""

    CONTENT = "0123456789"

    async def _revision(self, test_database, edition_id: str) -> int:
        async with test_database.get_session() as session:
            return (await session.execute_read(read_state, edition_id)).revision

    async def test_deleting_recording_advances_the_followed_edition(self, client, test_database):
        edition_id, audio_id = await self._setup_audio(client, test_database, self.CONTENT)
        recording_id = await self._create_recording(client, audio_id, {"edition_id": edition_id})
        await client.put(f"/v2/recordings/{recording_id}/cues", json=[_cue(0, 4, 0, 1200)])
        before = await self._revision(test_database, edition_id)

        assert (await client.delete(f"/v2/recordings/{recording_id}")).status_code == 204

        assert await self._revision(test_database, edition_id) == before + 1

    async def test_upload_to_missing_audio_stores_no_object(self, client, mock_storage):
        stored = len(mock_storage._storage)

        assert (await self._post_recording(client, generate_id(), {})).status_code == 404

        assert len(mock_storage._storage) == stored

    async def test_after_edition_delete_cues_read_empty_and_cannot_be_written(self, client, test_database):
        edition_id, audio_id = await self._setup_audio(client, test_database, self.CONTENT)
        recording_id = await self._create_recording(client, audio_id, {"edition_id": edition_id})
        assert (await client.delete(f"/v2/editions/{edition_id}")).status_code == 204

        assert (await client.get(f"/v2/recordings/{recording_id}/cues")).json() == []
        response = await client.put(f"/v2/recordings/{recording_id}/cues", json=[_cue(0, 4, 0, 1200)])
        assert response.status_code == 422

    @pytest.mark.parametrize("round_", range(3))
    async def test_cue_write_and_edition_delete_serialize(self, client, test_database, round_):
        edition_id, audio_id = await self._setup_audio(client, test_database, self.CONTENT)
        recording_id = await self._create_recording(client, audio_id, {"edition_id": edition_id})
        cue = RecordingCue.model_validate(_cue(0, 4, 0, 1200))

        cues, deleted = await asyncio.gather(
            test_database.recording.replace_cues(recording_id, [cue]),
            test_database.edition.delete(edition_id),
            return_exceptions=True,
        )

        # Either the cues land first and protect the edition, or the edition goes first and cues are refused.
        if cues is None:
            assert isinstance(deleted, DataConflictError), deleted
            assert await test_database.recording.get_cues(recording_id) == [cue]
        else:
            assert isinstance(cues, DataValidationError), cues
            assert deleted is None
            assert (await test_database.recording.get(recording_id)).edition_id is None

    @pytest.mark.parametrize("round_", range(3))
    async def test_recording_upload_and_audio_delete_serialize(self, client, test_database, round_):
        _, audio_id = await self._setup_audio(client, test_database, self.CONTENT)
        recording_id = generate_id()

        added, deleted = await asyncio.gather(
            test_database.recording.add(
                audio_id, RecordingInput(), recording_id, f"objects/{recording_id}", AudioFormat.MP3, 5
            ),
            test_database.audio.delete(audio_id),
            return_exceptions=True,
        )

        if added == recording_id:
            assert isinstance(deleted, DataConflictError), deleted
        else:
            assert isinstance(added, DataNotFoundError), added
            assert deleted is None
            assert await self._count(test_database, "Recording") == 0
