import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Path, status

from dependencies import get_db
from models.requests import LanguageCreateRequest
from models.responses import LanguageResponse

if TYPE_CHECKING:
    from database import Database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2/languages", tags=["Languages"])


@router.get(
    "",
    summary="List languages",
    description="Retrieve all available languages.",
)
async def get_all_languages(
    db: Annotated[Database, Depends(get_db)],
) -> list[LanguageResponse]:
    """List all available languages."""
    return await db.language.get_all()


@router.post(
    "",
    status_code=201,
    summary="Create language",
    description="Create a new language.",
)
async def create_language(
    data: LanguageCreateRequest,
    db: Annotated[Database, Depends(get_db)],
) -> LanguageResponse:
    """Create a new language."""
    created_code = await db.language.create(code=data.code, name=data.name)
    return LanguageResponse(code=created_code, name=data.name)


@router.delete(
    "/{code}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete language",
    description="Delete an unused language.",
)
async def delete_language(
    code: Annotated[str, Path(description="The language code")],
    db: Annotated[Database, Depends(get_db)],
) -> None:
    """Delete an unused language."""
    await db.language.delete(code)
