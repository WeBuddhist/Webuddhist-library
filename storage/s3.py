from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, BinaryIO, Self

import aioboto3
from botocore.exceptions import ClientError

from exceptions import DataNotFoundError

PRESIGNED_URL_TTL_SECONDS = 3600


@dataclass(eq=False, repr=False)
class Storage:
    bucket_name: str
    _client: Any

    @classmethod
    @asynccontextmanager
    async def open(cls, bucket_name: str, region: str) -> AsyncGenerator[Self]:
        async with aioboto3.Session().client("s3", region_name=region) as client:
            yield cls(bucket_name, client)

    async def put_immutable(self, key: str, body: bytes | BinaryIO, content_type: str) -> None:
        """Write a fresh object; never replace published bytes. Failures may leave an unused object."""
        await self._client.put_object(
            Bucket=self.bucket_name, Key=key, Body=body, ContentType=content_type, IfNoneMatch="*"
        )

    async def read_text(self, key: str) -> str:
        try:
            response = await self._client.get_object(Bucket=self.bucket_name, Key=key)
            async with response["Body"] as body:
                return (await body.read()).decode("utf-8")
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "NoSuchKey":
                raise DataNotFoundError(f"Content object '{key}' not found") from exc
            raise

    async def generate_url(self, key: str, expires_in: int = PRESIGNED_URL_TTL_SECONDS) -> str:
        return await self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket_name, "Key": key},
            ExpiresIn=expires_in,
        )
