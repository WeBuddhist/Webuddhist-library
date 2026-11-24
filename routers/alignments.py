import logging
from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from dependencies import DatabaseDep, OptionalAppHeader
from models.alignment import EditionAlignmentInput, EditionAlignmentPairOutput
from models.requests import AlignmentPaginationParams
from models.responses import PaginatedResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2/editions", tags=["Alignments"])


@router.put(
    "/{source_edition_id}/alignments/{target_edition_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Replace edition-pair alignments",
    description="Replace direct segment alignment relationships between two editions.",
)
async def replace_edition_pair_alignments(
    source_edition_id: Annotated[str, Path(description="The source edition ID")],
    target_edition_id: Annotated[str, Path(description="The target edition ID")],
    data: EditionAlignmentInput,
    db: DatabaseDep,
) -> None:
    await db.alignment.replace(source_edition_id, target_edition_id, data)


@router.get(
    "/{source_edition_id}/alignments/{target_edition_id}",
    summary="Get edition-pair alignments",
    description="Retrieve paginated direct segment alignment relationships between two editions.",
    response_model_exclude_none=True,
)
async def get_edition_pair_alignments(
    source_edition_id: Annotated[str, Path(description="The source edition ID")],
    target_edition_id: Annotated[str, Path(description="The target edition ID")],
    params: Annotated[AlignmentPaginationParams, Query()],
    db: DatabaseDep,
    x_application: OptionalAppHeader = None,
) -> PaginatedResponse[EditionAlignmentPairOutput]:
    alignments = await db.alignment.get(
        source_edition_id,
        target_edition_id,
        offset=params.offset,
        limit=params.limit + 1,
        application=x_application,
    )
    return PaginatedResponse.from_items(alignments, offset=params.offset, limit=params.limit)


@router.delete(
    "/{source_edition_id}/alignments/{target_edition_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete edition-pair alignments",
    description="Delete direct segment alignment relationships between two editions.",
)
async def delete_edition_pair_alignments(
    source_edition_id: Annotated[str, Path(description="The source edition ID")],
    target_edition_id: Annotated[str, Path(description="The target edition ID")],
    db: DatabaseDep,
) -> None:
    await db.alignment.delete(source_edition_id, target_edition_id)
