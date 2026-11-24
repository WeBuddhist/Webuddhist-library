from typing import Annotated

from fastapi import APIRouter, Path, status

from dependencies import DatabaseDep
from models.annotation import MarkOutput

router = APIRouter(prefix="/v2/yigchungs", tags=["Annotations"])


@router.get(
    "/{yigchung_id}",
    summary="Get yigchung annotation",
    description="Retrieve a yigchung mark annotation by ID.",
)
async def get_yigchung(
    yigchung_id: Annotated[str, Path(description="The ID of the yigchung annotation")],
    db: DatabaseDep,
) -> MarkOutput:
    return await db.annotation.mark.get(yigchung_id)


@router.delete(
    "/{yigchung_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete yigchung annotation",
    description="Delete a yigchung mark annotation.",
)
async def delete_yigchung(
    yigchung_id: Annotated[str, Path(description="The ID of the yigchung annotation")],
    db: DatabaseDep,
) -> None:
    await db.annotation.mark.delete(yigchung_id)
