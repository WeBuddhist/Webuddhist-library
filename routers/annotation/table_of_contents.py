import logging
from typing import Annotated

from fastapi import APIRouter, Path, status

from dependencies import DatabaseDep
from models.annotation import TableOfContentsOutput

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2/table-of-contents", tags=["Annotations"])


@router.get(
    "/{toc_id}",
    summary="Get table of contents",
    description="Retrieve a table of contents annotation by ID.",
    response_model_exclude_none=True,
)
async def get_table_of_contents(
    toc_id: Annotated[str, Path(description="The ID of the table of contents")],
    db: DatabaseDep,
) -> TableOfContentsOutput:
    return await db.annotation.table_of_contents.get(toc_id)


@router.delete(
    "/{toc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete table of contents",
    description="Delete a table of contents annotation.",
)
async def delete_table_of_contents(
    toc_id: Annotated[str, Path(description="The ID of the table of contents")],
    db: DatabaseDep,
) -> None:
    await db.annotation.table_of_contents.delete(toc_id)
