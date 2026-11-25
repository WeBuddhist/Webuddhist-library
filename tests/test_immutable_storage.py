import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiobotocore.httpchecksum import AioStreamingChecksumBody
from aiobotocore.session import AioSession, get_session
from aiobotocore.stub import AioStubber
from botocore.exceptions import ClientError, FlexibleChecksumError
from botocore.httpchecksum import Crc32Checksum

from config import Settings
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


async def test_read_text_rejects_an_invalid_s3_checksum_and_closes_the_response():
    raw = Mock()
    raw.content.read = AsyncMock(return_value=b"text")
    body = AioStreamingChecksumBody(raw, 4, Crc32Checksum(), "AAAAAA==")
    storage = Storage("bucket", SimpleNamespace(get_object=AsyncMock(return_value={"Body": body})))

    with pytest.raises(FlexibleChecksumError):
        await storage.read_text("edition")
    raw.close.assert_called_once_with()


@pytest.mark.parametrize(
    "code,status,expected",
    [("NoSuchKey", 404, DataNotFoundError), ("NoSuchBucket", 404, ClientError), ("AccessDenied", 403, ClientError)],
)
async def test_storage_read_preserves_error_meaning(code, status, expected):
    async with get_session().create_client(
        "s3", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test"
    ) as client:
        with AioStubber(client) as stub:
            stub.add_client_error(
                "get_object", code, http_status_code=status, expected_params={"Bucket": "bucket", "Key": "edition"}
            )
            with pytest.raises(expected) as caught:
                await Storage("bucket", client).read_text("edition")
        if code == "NoSuchKey":
            assert isinstance(caught.value.__cause__, client.exceptions.NoSuchKey)
            assert "edition" in str(caught.value)
        else:
            assert caught.value.response["Error"]["Code"] == code


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
    monkeypatch.setattr(AioSession, "create_client", factory)
    async with Storage.open(Settings(aws_s3_bucket="bucket", aws_region="region")) as storage:
        factory.assert_called_once_with("s3", region_name="region")
        assert storage.bucket_name == "bucket"
        assert storage._client is context.__aenter__.return_value
        context.__aexit__.assert_not_awaited()
    context.__aexit__.assert_awaited_once_with(None, None, None)
