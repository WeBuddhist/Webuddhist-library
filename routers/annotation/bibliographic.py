import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Path, status

from dependencies import get_db
from models.annotation import BibliographicMetadataOutput

if TYPE_CHECKING:
    from database import Database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2/bibliographic", tags=["Annotations"])


@router.get(
    "/{bibliographic_id}",
    summary="Get bibliographic metadata annotation",
    description="Retrieve a bibliographic metadata annotation by ID.",
)
async def get_bibliographic(
    bibliographic_id: Annotated[str, Path(description="The ID of the bibliographic annotation")],
    db: Annotated[Database, Depends(get_db)],
) -> BibliographicMetadataOutput:
    return await db.annotation.bibliographic.get(bibliographic_id)


@router.delete(
    "/{bibliographic_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete bibliographic metadata",
    description="Delete a bibliographic metadata annotation.",
)
async def delete_bibliographic(
    bibliographic_id: Annotated[str, Path(description="The ID of the bibliographic metadata")],
    db: Annotated[Database, Depends(get_db)],
) -> None:
    """Delete a bibliographic metadata annotation."""
    await db.annotation.bibliographic.delete(bibliographic_id)
