from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from content_service import read_segment_content
from dependencies import DatabaseDep, OptionalAppHeader, StorageDep
from exceptions import DataNotFoundError
from models.annotation import SegmentWithContextOutput
from models.requests import DirectRelatedSegmentsQueryParams
from models.responses import PaginatedResponse

SegmentId = Annotated[str, Path(description="The ID of the segment")]

router = APIRouter(prefix="/v2/segments", tags=["Segments"])


@router.get(
    "/{segment_id}/related",
    summary="Get related segments",
    description="Retrieve segments related to a given segment.",
)
async def get_related(
    segment_id: SegmentId,
    params: Annotated[DirectRelatedSegmentsQueryParams, Query()],
    db: DatabaseDep,
    x_application: OptionalAppHeader = None,
) -> PaginatedResponse[SegmentWithContextOutput]:
    """Get related segments."""
    try:
        segment = await db.segment.get(segment_id)
    except DataNotFoundError:
        return PaginatedResponse.from_items([], offset=params.offset, limit=params.limit)
    segments = await db.segment.get_related(
        edition_id=segment.edition_id,
        spans=[],
        starting_segment_id=segment_id,
        application=x_application,
        offset=params.offset,
        limit=params.limit + 1,
        filters=params,
    )
    return PaginatedResponse.from_items(segments, offset=params.offset, limit=params.limit)


@router.get(
    "/{segment_id}/content",
    summary="Get segment content",
    description="Retrieve the text content of a segment.",
)
async def get_segment_content(
    segment_id: SegmentId,
    db: DatabaseDep,
    storage: StorageDep,
    x_application: OptionalAppHeader = None,
) -> str:
    """Get segment content."""
    return await read_segment_content(db, storage, segment_id, x_application)


@router.post(
    "/{segment_id}/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Add tag to segment",
    description="Add a tag to a segment.",
)
async def tag_segment(
    segment_id: SegmentId,
    tag_id: Annotated[str, Path(description="The ID of the tag")],
    db: DatabaseDep,
    x_application: OptionalAppHeader = None,
) -> None:
    """Add a tag to a segment."""
    await db.tag.tag_segment(segment_id, tag_id, application=x_application)


@router.delete(
    "/{segment_id}/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove tag from segment",
    description="Remove a tag from a segment.",
)
async def untag_segment(
    segment_id: SegmentId,
    tag_id: Annotated[str, Path(description="The ID of the tag")],
    db: DatabaseDep,
    x_application: OptionalAppHeader = None,
) -> None:
    """Remove a tag from a segment."""
    await db.tag.untag_segment(segment_id, tag_id, application=x_application)


@router.get(
    "/{segment_id}",
    summary="Get segment",
    description="Retrieve a segment with edition, text, segmentation, lines, and tag context.",
    response_model_exclude_none=True,
)
async def get_segment(
    segment_id: SegmentId,
    db: DatabaseDep,
    x_application: OptionalAppHeader = None,
) -> SegmentWithContextOutput:
    """Get a segment with context."""
    return await db.segment.get(segment_id, application=x_application)
