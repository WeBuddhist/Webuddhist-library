import logging
from typing import Annotated

from fastapi import APIRouter, Path, status

from database import language
from dependencies import DatabaseDep
from models.requests import LanguageCreateRequest
from models.responses import LanguageResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v2/languages", tags=["Languages"])


@router.get("", summary="List languages", description="Retrieve all available languages.")
async def get_all_languages(db: DatabaseDep) -> list[LanguageResponse]:
    return await language.get_all(db)


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create language", description="Create a new language.")
async def create_language(data: LanguageCreateRequest, db: DatabaseDep) -> LanguageResponse:
    created_code = await language.create(db, code=data.code, name=data.name)
    return LanguageResponse(code=created_code, name=data.name)


@router.delete(
    "/{code}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete language",
    description="Delete an unused language.",
)
async def delete_language(code: Annotated[str, Path(description="The language code")], db: DatabaseDep) -> None:
    await language.delete(db, code)
