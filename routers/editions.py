import logging
from typing import Annotated

from fastapi import APIRouter, Body, Path, Query, status

from content_service import edit_content, read_content, sign_edition_content_url
from dependencies import DatabaseDep, StorageDep
from models.alignment import EditionAlignmentOutput
from models.annotation import (
    BibliographicMetadataInput,
    BibliographicMetadataOutput,
    FootnoteInput,
    FootnoteOutput,
    MarkInput,
    MarkOutput,
    NoteInput,
    NoteOutput,
    PaginationInput,
    PaginationOutput,
    SegmentationInput,
    SegmentationOutput,
    SegmentOutput,
    TableOfContentsInput,
    TableOfContentsOutput,
)
from models.content_operation import ContentOperation
from models.edition import EditionOutput
from models.enums import NoteType
from models.recording import RecordingOutput
from models.requests import AnnotationSegmentsPaginationParams
from models.responses import IdResponse, PaginatedResponse, PresignedUrlResponse
from search_updates import SearchUpdatesDep

logger = logging.getLogger(__name__)

EditionId = Annotated[str, Path(description="The ID of the edition")]

router = APIRouter(prefix="/v2/editions", tags=["Editions"])


@router.get(
    "/{edition_id}",
    summary="Get edition metadata",
    description="Retrieve metadata for a specific edition.",
)
async def get_metadata(edition_id: EditionId, db: DatabaseDep) -> EditionOutput:
    """Fetch metadata for an edition."""
    logger.info("Fetching metadata for edition %s", edition_id)
    return await db.edition.get(edition_id=edition_id)


@router.get(
    "/{edition_id}/content",
    summary="Get edition content",
    description="Retrieve the base text content of an edition.",
)
async def get_content(
    edition_id: EditionId,
    db: DatabaseDep,
    storage: StorageDep,
    span_start: Annotated[int | None, Query(description="Start position for text slice")] = None,
    span_end: Annotated[int | None, Query(description="End position for text slice")] = None,
) -> str:
    """Fetch base text content for an edition."""
    return await read_content(db, storage, edition_id, span_start=span_start, span_end=span_end)


@router.get(
    "/{edition_id}/content/url",
    summary="Get edition content URL",
    description="Generate a short-lived presigned URL for downloading the full base text directly from storage.",
)
async def get_content_url(
    edition_id: EditionId,
    db: DatabaseDep,
    storage: StorageDep,
) -> PresignedUrlResponse:
    """Generate a direct download URL for an edition's full base text."""
    url, expires_at = await sign_edition_content_url(db, storage, edition_id)
    return PresignedUrlResponse(url=url, expires_at=expires_at)


@router.get(
    "/{edition_id}/segmentation",
    summary="Get edition segmentation",
    description="Retrieve the edition's segmentation.",
    response_model_exclude_none=True,
)
async def get_segmentation_annotation(edition_id: EditionId, db: DatabaseDep) -> SegmentationOutput:
    return await db.annotation.segmentation.get_by_edition(edition_id)


@router.post(
    "/{edition_id}/segmentation",
    status_code=status.HTTP_201_CREATED,
    summary="Add edition segmentation",
    description="Add the edition's segmentation.",
)
async def post_segmentation_annotation(
    search: SearchUpdatesDep,
    edition_id: EditionId,
    data: SegmentationInput,
    db: DatabaseDep,
) -> IdResponse:
    """Add a segmentation annotation to an edition."""
    annotation_id = await db.annotation.segmentation.add(edition_id, data)
    search.schedule("edition", edition_id)
    return IdResponse(id=annotation_id)


@router.get(
    "/{edition_id}/segmentation/segments",
    summary="Get edition segmentation segments",
    description="Retrieve paginated segments for the edition's segmentation.",
    response_model_exclude_none=True,
)
async def get_segmentation_segments(
    edition_id: EditionId,
    params: Annotated[AnnotationSegmentsPaginationParams, Query()],
    db: DatabaseDep,
) -> PaginatedResponse[SegmentOutput]:
    segments = await db.annotation.segmentation.get_segments_by_edition(
        edition_id,
        offset=params.offset,
        limit=params.limit + 1,
    )
    return PaginatedResponse.from_items(segments, offset=params.offset, limit=params.limit)


@router.delete(
    "/{edition_id}/segmentation",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete edition segmentation",
    description="Delete the edition's segmentation.",
)
async def delete_segmentation_annotation(
    search: SearchUpdatesDep,
    edition_id: EditionId,
    db: DatabaseDep,
) -> None:
    await db.annotation.segmentation.delete_by_edition(edition_id)
    search.schedule("edition", edition_id)


@router.get(
    "/{edition_id}/alignments",
    summary="Get edition alignments",
    description="Retrieve directional alignment contexts that include this edition.",
)
async def get_alignment_annotations(
    edition_id: EditionId,
    db: DatabaseDep,
) -> list[EditionAlignmentOutput]:
    return await db.alignment.get_all_for_edition(edition_id)


@router.get(
    "/{edition_id}/pagination",
    summary="Get pagination annotations",
    description="Retrieve pagination annotation for an edition.",
)
async def get_pagination_annotation(
    edition_id: EditionId,
    db: DatabaseDep,
) -> PaginationOutput | None:
    return await db.annotation.pagination.get_all(edition_id)


@router.post(
    "/{edition_id}/pagination",
    status_code=status.HTTP_201_CREATED,
    summary="Add pagination annotation",
    description="Add a pagination annotation to an edition.",
)
async def post_pagination_annotation(
    edition_id: EditionId,
    data: PaginationInput,
    db: DatabaseDep,
) -> IdResponse:
    """Add a pagination annotation to an edition."""
    return IdResponse(id=await db.annotation.pagination.add(edition_id, data))


@router.get(
    "/{edition_id}/table-of-contents",
    summary="Get table of contents annotations",
    description="Retrieve all table of contents annotations for an edition.",
    response_model_exclude_none=True,
)
async def get_table_of_contents_annotations(
    edition_id: EditionId,
    db: DatabaseDep,
) -> list[TableOfContentsOutput]:
    return await db.annotation.table_of_contents.get_all(edition_id)


@router.post(
    "/{edition_id}/table-of-contents",
    status_code=status.HTTP_201_CREATED,
    summary="Add table of contents annotation",
    description="Add a table of contents annotation to an edition.",
)
async def post_table_of_contents_annotation(
    edition_id: EditionId,
    data: TableOfContentsInput,
    db: DatabaseDep,
) -> IdResponse:
    return IdResponse(id=await db.annotation.table_of_contents.add(edition_id, data))


@router.get(
    "/{edition_id}/bibliographic",
    summary="Get bibliographic metadata annotations",
    description="Retrieve all bibliographic metadata annotations for an edition.",
)
async def get_bibliographic_annotations(
    edition_id: EditionId,
    db: DatabaseDep,
) -> list[BibliographicMetadataOutput]:
    return await db.annotation.bibliographic.get_all(edition_id)


@router.post(
    "/{edition_id}/bibliographic",
    status_code=status.HTTP_201_CREATED,
    summary="Add bibliographic metadata annotation",
    description="Add bibliographic metadata annotation to an edition.",
)
async def post_bibliographic_annotation(
    edition_id: EditionId,
    data: BibliographicMetadataInput,
    db: DatabaseDep,
) -> IdResponse:
    return IdResponse(id=await db.annotation.bibliographic.add(edition_id, data))


@router.get(
    "/{edition_id}/durchens",
    summary="Get durchen annotations",
    description="Retrieve all durchen annotations for an edition.",
)
async def get_durchen_annotations(edition_id: EditionId, db: DatabaseDep) -> list[NoteOutput]:
    return await db.annotation.note.get_all(edition_id, NoteType.DURCHEN)


@router.post(
    "/{edition_id}/durchens",
    status_code=status.HTTP_201_CREATED,
    summary="Add durchen annotations",
    description="Atomically add one or more durchen annotations to an edition.",
)
async def post_durchen_annotations(
    edition_id: EditionId,
    data: Annotated[list[NoteInput], Body(min_length=1)],
    db: DatabaseDep,
) -> list[IdResponse]:
    annotation_ids = await db.annotation.note.add_durchens(edition_id, data)
    return [IdResponse(id=annotation_id) for annotation_id in annotation_ids]


@router.get(
    "/{edition_id}/footnotes",
    summary="Get footnote annotations",
    description="Retrieve all footnotes (citations, author's notes, etc.) for an edition, ordered by position.",
)
async def get_footnote_annotations(edition_id: EditionId, db: DatabaseDep) -> list[FootnoteOutput]:
    notes = await db.annotation.note.get_all(edition_id, NoteType.FOOTNOTE)
    return [FootnoteOutput.from_note(note) for note in notes]


@router.post(
    "/{edition_id}/footnotes",
    status_code=status.HTTP_201_CREATED,
    summary="Add footnote annotations",
    description=(
        "Atomically add one or more footnotes to an edition. `position` is a gap index from 0 (before the "
        "first character) to the content length (after the last character); the marker sits before the "
        "character at `position`. Adding footnotes does not change the content."
    ),
)
async def post_footnote_annotations(
    edition_id: EditionId,
    data: Annotated[list[FootnoteInput], Body(min_length=1)],
    db: DatabaseDep,
) -> list[IdResponse]:
    annotation_ids = await db.annotation.note.add_footnotes(edition_id, data)
    return [IdResponse(id=annotation_id) for annotation_id in annotation_ids]


@router.delete(
    "/{edition_id}/footnotes",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete all footnote annotations",
    description="Delete every footnote on an edition in one transaction. Durchen notes are not affected.",
)
async def delete_footnote_annotations(edition_id: EditionId, db: DatabaseDep) -> None:
    await db.annotation.note.delete_all_footnotes(edition_id)


@router.get(
    "/{edition_id}/yigchungs",
    summary="Get yigchung annotations",
    description="Retrieve all yigchung mark annotations for an edition.",
)
async def get_yigchung_annotations(edition_id: EditionId, db: DatabaseDep) -> list[MarkOutput]:
    return await db.annotation.mark.get_all(edition_id)


@router.post(
    "/{edition_id}/yigchungs",
    status_code=status.HTTP_201_CREATED,
    summary="Add yigchung annotations",
    description="Atomically add one or more span-only yigchung mark annotations to an edition.",
)
async def post_yigchung_annotations(
    edition_id: EditionId,
    data: Annotated[list[MarkInput], Body(min_length=1)],
    db: DatabaseDep,
) -> list[IdResponse]:
    annotation_ids = await db.annotation.mark.add_yigchungs(edition_id, data)
    return [IdResponse(id=annotation_id) for annotation_id in annotation_ids]


@router.get(
    "/{edition_id}/recordings",
    summary="Get edition recordings",
    description="Retrieve the recordings that follow an edition, for follow-along playback.",
    response_model_exclude_none=True,
)
async def get_recordings(edition_id: EditionId, db: DatabaseDep) -> list[RecordingOutput]:
    return await db.recording.get_all_by_edition(edition_id)


@router.get(
    "/{edition_id}/related",
    summary="Get related editions",
    description="Find editions related to a given edition.",
)
async def get_related_editions(edition_id: EditionId, db: DatabaseDep) -> list[EditionOutput]:
    logger.info("Finding related editions for edition ID: %s", edition_id)
    return await db.edition.get_related(edition_id)


@router.delete(
    "/{edition_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete edition",
    description=(
        "Delete edition metadata and associated database annotations. Recordings that follow the edition stay; "
        "the request is rejected while any of them has cues."
    ),
)
async def delete_edition(search: SearchUpdatesDep, edition_id: EditionId, db: DatabaseDep) -> None:
    logger.info("Deleting edition with edition ID: %s", edition_id)
    await db.edition.delete(edition_id)
    search.schedule("edition", edition_id)


@router.patch(
    "/{edition_id}/content",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Patch edition content",
    description="Apply a text operation (INSERT, DELETE, or REPLACE) to the edition's content.",
)
async def patch_content(
    search: SearchUpdatesDep,
    edition_id: EditionId,
    data: ContentOperation,
    db: DatabaseDep,
    storage: StorageDep,
) -> None:
    await edit_content(db, storage, edition_id, data)
    search.schedule("edition", edition_id)
