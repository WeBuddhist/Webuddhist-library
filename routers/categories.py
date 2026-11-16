import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from dependencies import RequiredAppHeader, get_api_key, get_db
from exceptions import DataNotFoundError
from models.category import CategoryInput, CategoryOutput, CategoryPatch
from models.responses import IdResponse

if TYPE_CHECKING:
    from database import Database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2/categories", tags=["Categories"])


@router.get(
    "",
    summary="List categories",
    description="Retrieve categories for an application.",
)
async def get_categories(
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    x_application: RequiredAppHeader,
    parent_id: Annotated[str | None, Query(description="Filter by parent category ID")] = None,
) -> list[CategoryOutput]:
    """List categories for an application."""
    if not await db.application.exists(x_application):
        raise DataNotFoundError(f"Application '{x_application}' not found")

    return await db.category.get_all(application=x_application, parent_id=parent_id)


@router.get(
    "/{category_id}",
    summary="Get category by ID",
    description="Retrieve a single category by its ID for an application.",
)
async def get_category_by_id(
    category_id: str,
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    x_application: RequiredAppHeader,
) -> CategoryOutput:
    """Get a single category by ID."""
    if not await db.application.exists(x_application):
        raise DataNotFoundError(f"Application '{x_application}' not found")

    category = await db.category.get_by_id(category_id, application=x_application)
    if category is None:
        raise DataNotFoundError(f"Category '{category_id}' not found")

    return category


@router.post(
    "",
    status_code=201,
    summary="Create category",
    description="Create a new category for an application.",
)
async def create_category(
    data: CategoryInput,
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    x_application: RequiredAppHeader,
) -> IdResponse:
    """Create a new category."""
    if not await db.application.exists(x_application):
        raise DataNotFoundError(f"Application '{x_application}' not found")

    category_id = await db.category.create(data, application=x_application)
    return IdResponse(id=category_id)


@router.patch(
    "/{category_id}",
    summary="Update category localizations",
    description=(
        "Add or replace title and description localizations of a category. "
        "Languages sent are added or replaced; languages not sent are kept."
    ),
)
async def update_category(
    category_id: Annotated[str, Path(description="The ID of the category")],
    data: CategoryPatch,
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    x_application: RequiredAppHeader,
) -> CategoryOutput:
    """Update a category's title and description localizations."""
    if not await db.application.exists(x_application):
        raise DataNotFoundError(f"Application '{x_application}' not found")

    logger.info("Updating category %s with: %s", category_id, data.model_dump_json())
    return await db.category.update(category_id, data, application=x_application)


@router.delete(
    "/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete category",
    description="Delete a category and its child categories for an application.",
)
async def delete_category(
    category_id: Annotated[str, Path(description="The ID of the category")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    x_application: RequiredAppHeader,
) -> None:
    """Delete a category and its child categories."""
    if not await db.application.exists(x_application):
        raise DataNotFoundError(f"Application '{x_application}' not found")

    await db.category.delete(category_id, application=x_application)
