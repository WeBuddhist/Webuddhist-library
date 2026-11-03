import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Path, status

from dependencies import get_api_key, get_db
from models.annotation import NoteOutput
from models.enums import NoteType

if TYPE_CHECKING:
    from database import Database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2/durchens", tags=["Annotations"])


@router.get(
    "/{durchen_id}",
    summary="Get durchen annotation",
    description="Retrieve a durchen annotation by ID.",
)
async def get_durchen(
    durchen_id: Annotated[str, Path(description="The ID of the durchen annotation")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> NoteOutput:
    """Get a durchen annotation."""
    return await db.annotation.note.get(durchen_id, NoteType.DURCHEN)


@router.delete(
    "/{durchen_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete durchen note",
    description="Delete a durchen note annotation.",
)
async def delete_durchen(
    durchen_id: Annotated[str, Path(description="The ID of the durchen note")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> None:
    """Delete a durchen note annotation."""
    await db.annotation.note.delete(durchen_id, NoteType.DURCHEN)
