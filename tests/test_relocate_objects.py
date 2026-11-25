import io
from unittest.mock import AsyncMock, Mock

import pytest
from aiobotocore.response import AioStreamingBody
from botocore.exceptions import ClientError

from content_service import read_content
from database import recording as recording_database
from errors import DataConflictError, DataValidationError
from scripts.relocate_objects import RelocationStorage, relocate_objects
from tests.test_content_lifecycle import state
from tests.test_excerpt_editions import WHOLE, _part
from tests.test_recordings import RecordingHelpers

pytestmark = pytest.mark.asyncio(loop_scope="session")


class MemoryS3:
    def __init__(self, objects):
        self.objects = objects

    async def get_object(self, *, Bucket, Key):
        mime, data = self.objects[Key]
        response = Mock()
        response.content.read = AsyncMock(side_effect=io.BytesIO(data).read)
        return {"ContentType": mime, "Body": AioStreamingBody(response, str(len(data)))}

    async def copy_object(self, *, Bucket, CopySource, Key, IfNoneMatch):
        assert CopySource["Bucket"] == Bucket and IfNoneMatch == "*"
        if Key in self.objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "CopyObject")
        self.objects[Key] = self.objects[CopySource["Key"]]

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    async def paginate(self, *, Bucket, Prefix):
        assert Prefix == "objects/"
        keys = sorted(key for key in self.objects if key.startswith(Prefix))
        for key in keys:  # One key per page to exercise pagination during deletion.
            yield {"Contents": [{"Key": key}]}

    async def list_objects_v2(self, *, Bucket, Prefix, MaxKeys):
        return {"KeyCount": min(MaxKeys, sum(k.startswith(Prefix) for k in self.objects))}

    async def delete_objects(self, *, Bucket, Delete):
        for entry in Delete["Objects"]:
            key = entry["Key"]
            assert key.startswith("objects/")
            self.objects.pop(key, None)
        return {}


async def test_preview_collision_retry_and_cleanup(client, test_database):
    db = test_database
    setup = RecordingHelpers()
    edition, audio = await setup._setup_audio(client, db)
    recording = await setup._create_recording(client, audio)
    original = await state(db, edition)
    old_text, old_audio = "objects/old-edit", f"objects/{recording}"
    text_key = f"base_texts/{original.text_id}/{edition}/old-edit.txt"
    audio_key = f"recordings/{recording}.mp3"
    async with db.get_session() as session:
        await session.run(
            "MATCH (e:Edition {id: $edition}), (r:Recording {id: $recording}) "
            "SET e.content_key = $text_key, r.audio_key = $audio_key",
            edition=edition,
            recording=recording,
            text_key=old_text,
            audio_key=old_audio,
        )
    originals = {
        old_text: ("text/plain; charset=utf-8", "Sample text content".encode()),
        old_audio: ("audio/mpeg", b"\xff\x00"),
        "objects/abandoned": ("text/plain", b"unreferenced"),
        original.stored_key: ("text/plain", b"retain existing edition version"),
        audio_key: ("audio/mpeg", b"conflicting file"),
    }
    s3 = MemoryS3(originals.copy())
    storage = RelocationStorage("test", s3)
    assert await relocate_objects(db, storage) == (2, 3)
    assert s3.objects == originals and (await state(db, edition)).stored_key == old_text
    with pytest.raises(DataConflictError, match="verification"):
        await relocate_objects(db, storage, apply=True)
    assert s3.objects == {**originals, text_key: originals[old_text]}
    assert await recording_database.get_storage_location(db, recording) == old_audio
    moved = await state(db, edition)
    assert moved.stored_key == text_key and moved.revision == original.revision + 1

    # A matching destination can be left by an interruption just before the pointer switch.
    s3.objects[audio_key] = originals[old_audio]
    assert await relocate_objects(db, storage, apply=True) == (1, 3)
    assert s3.objects == {
        text_key: originals[old_text],
        audio_key: originals[old_audio],
        original.stored_key: originals[original.stored_key],
    }
    assert await recording_database.get_storage_location(db, recording) == audio_key
    assert await relocate_objects(db, storage, apply=True) == (0, 0)
    assert await state(db, edition) == moved


async def test_partial_s3_deletion_fails_and_reruns(test_database, monkeypatch):
    s3 = MemoryS3({"objects/leftover": ("audio/mpeg", b"orphan")})
    storage = RelocationStorage("test", s3)
    with monkeypatch.context() as patch:
        patch.setattr(s3, "delete_objects", AsyncMock(return_value={"Errors": [{"Code": "AccessDenied"}]}))
        with pytest.raises(DataConflictError, match="AccessDenied"):
            await relocate_objects(test_database, storage, apply=True)
        # Even a successful delete response must be followed by an empty listing.
        patch.setattr(s3, "delete_objects", AsyncMock(return_value={}))
        with pytest.raises(DataConflictError, match="Files remain"):
            await relocate_objects(test_database, storage, apply=True)
    assert await relocate_objects(test_database, storage, apply=True) == (0, 1)
    assert not s3.objects


async def test_cleanup_rechecks_graph_after_copying_recordings(client, test_database):
    db = test_database
    setup = RecordingHelpers()
    edition, audio = await setup._setup_audio(client, db)
    recording = await setup._create_recording(client, audio)
    async with db.get_session() as session:
        await session.run("MATCH (r:Recording {id: $id}) SET r.audio_key = 'objects/audio'", id=recording)

    async def copy_with_late_writer(source, destination):
        # Edition scanning is already complete when a recording is being copied.
        async with db.get_session() as session:
            await session.run("MATCH (e:Edition {id: $id}) SET e.content_key = 'objects/late'", id=edition)

    storage = AsyncMock(spec=RelocationStorage)
    storage.copy_verified.side_effect = copy_with_late_writer
    with pytest.raises(DataConflictError, match="still references"):
        await relocate_objects(db, storage, apply=True)
    storage.legacy_objects.assert_not_awaited()


async def test_legacy_pointer_without_id_blocks_preview_and_cleanup(client, test_database):
    edition, _ = await RecordingHelpers()._setup_audio(client, test_database)
    async with test_database.get_session() as session:
        await session.run("MATCH (e:Edition {id: $id}) SET e.content_key = 'objects/unpaged' REMOVE e.id", id=edition)
    storage = AsyncMock(spec=RelocationStorage)
    for apply in (False, True):
        with pytest.raises(DataValidationError, match="Edition pointing to 'objects/unpaged' has no ID"):
            await relocate_objects(test_database, storage, apply=apply)
    storage.legacy_objects.assert_not_awaited()


async def test_excerpt_reads_relocated_source_without_own_key(client, test_database):
    _, edition, _, excerpt = await _part(client, test_database)
    source = f"objects/{edition}"
    async with test_database.get_session() as session:
        await session.run("MATCH (e:Edition {id: $id}) SET e.content_key = $source", id=edition, source=source)
    storage = RelocationStorage("test", MemoryS3({source: ("text/plain", WHOLE.encode())}))
    assert await relocate_objects(test_database, storage) == (1, 1)
    assert await relocate_objects(test_database, storage, apply=True) == (1, 1)
    assert await read_content(test_database, storage, excerpt) == "Two. "
