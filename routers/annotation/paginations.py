import logging
from typing import Annotated

from fastapi import APIRouter, Path, status

from dependencies import DatabaseDep
from models.annotation import PaginationOutput

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2/paginations", tags=["Annotations"])


@router.get(
    "/{pagination_id}",
    summary="Get pagination",
    description="Retrieve a pagination annotation by ID.",
)
async def get_pagination(
    pagination_id: Annotated[str, Path(description="The ID of the pagination")],
    db: DatabaseDep,
) -> PaginationOutput:
    return await db.annotation.pagination.get(pagination_id)


@router.delete(
    "/{pagination_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete pagination",
    description="Delete a pagination annotation.",
)
async def delete_pagination(
    pagination_id: Annotated[str, Path(description="The ID of the pagination")],
    db: DatabaseDep,
) -> None:
    """Delete a pagination annotation."""
    await db.annotation.pagination.delete(pagination_id)
