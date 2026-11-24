import logging
from typing import Annotated

from fastapi import APIRouter, Path, status

from dependencies import DatabaseDep
from models.annotation import FootnoteOutput
from models.enums import NoteType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2/footnotes", tags=["Annotations"])


@router.get(
    "/{footnote_id}",
    summary="Get footnote annotation",
    description="Retrieve a footnote annotation by ID.",
)
async def get_footnote(
    footnote_id: Annotated[str, Path(description="The ID of the footnote annotation")],
    db: DatabaseDep,
) -> FootnoteOutput:
    """Get a footnote annotation."""
    return FootnoteOutput.from_note(await db.annotation.note.get(footnote_id, NoteType.FOOTNOTE))


@router.delete(
    "/{footnote_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete footnote annotation",
    description="Delete a footnote annotation.",
)
async def delete_footnote(
    footnote_id: Annotated[str, Path(description="The ID of the footnote annotation")],
    db: DatabaseDep,
) -> None:
    """Delete a footnote annotation."""
    await db.annotation.note.delete(footnote_id, NoteType.FOOTNOTE)
