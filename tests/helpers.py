from typing import TYPE_CHECKING

from fastapi import FastAPI

from database import segment
from database.annotation import segmentation
from database.content_state import read_value
from dependencies import get_api_key
from main import create_app
from models.annotation import SegmentOutput, SegmentWithContextOutput
from models.requests import RelatedSegmentsFilter

if TYPE_CHECKING:
    from database import Database


def create_unauthenticated_app() -> FastAPI:
    """The API with API key checks bypassed, for tests that exercise handlers."""
    app = create_app()
    app.dependency_overrides[get_api_key] = lambda: "test-api-key"
    return app


async def get_all_segments_by_edition(db: Database, edition_id: str) -> list[SegmentOutput]:
    return await db.read(read_value, edition_id, lambda tx: segmentation._segments(tx, edition_id))


async def find_by_span(db: Database, edition_id: str, start: int, end: int) -> list[str]:
    return await db.read(segment._find_by_spans, edition_id, [(start, end)])


async def get_related_by_spans(
    db: Database,
    edition_id: str,
    spans: list[tuple[int, int]],
    *,
    application: str | None = None,
    offset: int = 0,
    limit: int = 20,
    filters: RelatedSegmentsFilter | None = None,
) -> list[SegmentWithContextOutput]:
    return await db.read(
        read_value,
        edition_id,
        lambda tx: segment.get_related_by_spans(
            tx, edition_id, spans, application=application, offset=offset, limit=limit, filters=filters
        ),
    )
