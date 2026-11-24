import logging
from typing import Annotated

from fastapi import APIRouter, Path, status

from dependencies import DatabaseDep
from models.annotation import NoteOutput
from models.enums import NoteType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2/durchens", tags=["Annotations"])


@router.get(
    "/{durchen_id}",
    summary="Get durchen annotation",
    description="Retrieve a durchen annotation by ID.",
)
async def get_durchen(
    durchen_id: Annotated[str, Path(description="The ID of the durchen annotation")],
    db: DatabaseDep,
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
    db: DatabaseDep,
) -> None:
    """Delete a durchen note annotation."""
    await db.annotation.note.delete(durchen_id, NoteType.DURCHEN)
