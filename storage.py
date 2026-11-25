from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, BinaryIO, Self

from aiobotocore.session import get_session

from errors import DataNotFoundError
from models.enums import AudioFormat

if TYPE_CHECKING:
    from config import Settings

PRESIGNED_URL_TTL_SECONDS = 3600


def edition_key(text_id: str, edition_id: str, version: str | None = None) -> str:
    path = f"base_texts/{text_id}/{edition_id}"
    return f"{path}.txt" if version is None else f"{path}/{version}.txt"


def recording_key(recording_id: str, audio_format: AudioFormat) -> str:
    return f"recordings/{recording_id}.{audio_format.value}"


@dataclass(eq=False, repr=False)
class Storage:
    bucket_name: str
    _client: Any

    @classmethod
    @asynccontextmanager
    async def open(cls, config: Settings) -> AsyncGenerator[Self]:
        async with get_session().create_client("s3", region_name=config.aws_region) as client:
            yield cls(config.aws_s3_bucket, client)

    async def put_immutable(self, key: str, body: bytes | BinaryIO, content_type: str) -> None:
        """Write a fresh object; never replace published bytes. Failures may leave an unused object."""
        await self._client.put_object(
            Bucket=self.bucket_name, Key=key, Body=body, ContentType=content_type, IfNoneMatch="*"
        )

    async def read_text(self, key: str) -> str:
        try:
            response = await self._client.get_object(Bucket=self.bucket_name, Key=key)
        except self._client.exceptions.NoSuchKey as exc:
            raise DataNotFoundError(f"Content object '{key}' not found") from exc
        async with response["Body"] as body:
            return (await body.read()).decode("utf-8")

    async def generate_url(self, key: str, expires_in: int = PRESIGNED_URL_TTL_SECONDS) -> str:
        return await self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket_name, "Key": key},
            ExpiresIn=expires_in,
        )
