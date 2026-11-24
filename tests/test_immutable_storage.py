import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aioboto3
import pytest
from botocore.exceptions import ClientError

from storage import Storage
from exceptions import DataNotFoundError


def precondition_failed():
    return ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")


async def test_upload_refuses_to_overwrite_an_existing_key():
    objects = {}

    async def put_object(**request):
        assert request["Bucket"] == "bucket"
        assert request["ContentType"] == "text/plain"
        if request.get("IfNoneMatch") == "*" and request["Key"] in objects:
            raise precondition_failed()
        objects[request["Key"]] = request["Body"]

    storage = Storage("bucket", SimpleNamespace(put_object=put_object))
    await storage.put_immutable("key", b"original", "text/plain")
    with pytest.raises(ClientError, match="PreconditionFailed"):
        await storage.put_immutable("key", b"replacement", "text/plain")
    assert objects == {"key": b"original"}


async def test_audio_body_is_passed_as_a_stream_without_copying():
    reads = []

    class SizedReadsOnly(io.BytesIO):
        def read(self, size=-1):
            assert 0 <= size <= 65536
            reads.append(size)
            return super().read(size)

    audio = SizedReadsOnly(b"x" * (1024 * 1024))

    async def put_object(**kwargs):
        assert kwargs["Bucket"] == "bucket"
        assert kwargs["Key"] == "audio"
        assert kwargs["ContentType"] == "audio/mpeg"
        assert kwargs["Body"] is audio
        assert kwargs["IfNoneMatch"] == "*"
        while kwargs["Body"].read(65536):
            pass

    storage = Storage("bucket", SimpleNamespace(put_object=put_object))
    await storage.put_immutable("audio", audio, "audio/mpeg")
    assert len(reads) > 1


@pytest.mark.parametrize("payload", ["བོད་🙂e\u0301".encode(), b"\xff"])
async def test_read_text_decodes_utf8_and_closes_body(payload):
    body = AsyncMock()
    body.__aenter__.return_value = body
    body.read.return_value = payload
    storage = Storage("bucket", SimpleNamespace(get_object=AsyncMock(return_value={"Body": body})))
    if payload == b"\xff":
        with pytest.raises(UnicodeDecodeError):
            await storage.read_text("edition")
    else:
        assert await storage.read_text("edition") == "བོད་🙂e\u0301"
    storage._client.get_object.assert_awaited_once_with(Bucket="bucket", Key="edition")
    body.__aexit__.assert_awaited_once()


@pytest.mark.parametrize("code,expected", [("NoSuchKey", DataNotFoundError), ("AccessDenied", ClientError)])
async def test_storage_read_preserves_error_meaning(code, expected):
    error = ClientError({"Error": {"Code": code}}, "GetObject")
    storage = Storage("bucket", SimpleNamespace(get_object=AsyncMock(side_effect=error)))
    with pytest.raises(expected) as caught:
        await storage.read_text("edition")
    if code == "NoSuchKey":
        assert caught.value.__cause__ is error
        assert "edition" in str(caught.value)
    else:
        assert caught.value is error


@pytest.mark.parametrize("expiry", [None, 90])
async def test_signed_url_uses_requested_key_and_expiry(expiry):
    storage = Storage("bucket", SimpleNamespace(generate_presigned_url=AsyncMock(return_value="https://signed")))
    args = {} if expiry is None else {"expires_in": expiry}
    assert await storage.generate_url("audio/recording.mp3", **args) == "https://signed"
    storage._client.generate_presigned_url.assert_awaited_once_with(
        "get_object",
        Params={"Bucket": "bucket", "Key": "audio/recording.mp3"},
        ExpiresIn=3600 if expiry is None else expiry,
    )


async def test_storage_connection_is_closed_once(monkeypatch):
    context = AsyncMock()
    factory = Mock(return_value=context)
    monkeypatch.setattr(aioboto3.Session, "client", factory)
    async with Storage.open("bucket", "region") as storage:
        factory.assert_called_once_with("s3", region_name="region")
        assert storage._client is context.__aenter__.return_value
        context.__aexit__.assert_not_awaited()
    context.__aexit__.assert_awaited_once_with(None, None, None)
