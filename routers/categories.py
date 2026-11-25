import logging
from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from dependencies import ApplicationDep, DatabaseDep
from models.category import CategoryInput, CategoryOutput, CategoryPatch
from models.responses import IdResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2/categories", tags=["Categories"])


@router.get(
    "",
    summary="List categories",
    description="Retrieve categories for an application.",
)
async def get_categories(
    db: DatabaseDep,
    x_application: ApplicationDep,
    parent_id: Annotated[str | None, Query(description="Filter by parent category ID")] = None,
) -> list[CategoryOutput]:
    """List categories for an application."""
    return await db.category.get_all(application=x_application, parent_id=parent_id)


@router.get(
    "/{category_id}",
    summary="Get category by ID",
    description="Retrieve a single category by its ID for an application.",
)
async def get_category_by_id(category_id: str, db: DatabaseDep, x_application: ApplicationDep) -> CategoryOutput:
    """Get a single category by ID."""
    return await db.category.get_by_id(category_id, application=x_application)


@router.post(
    "",
    status_code=201,
    summary="Create category",
    description="Create a new category for an application.",
)
async def create_category(data: CategoryInput, db: DatabaseDep, x_application: ApplicationDep) -> IdResponse:
    """Create a new category."""
    return IdResponse(id=await db.category.create(data, application=x_application))


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
    db: DatabaseDep,
    x_application: ApplicationDep,
) -> CategoryOutput:
    """Update a category's title and description localizations."""
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
    db: DatabaseDep,
    x_application: ApplicationDep,
) -> None:
    """Delete a category and its child categories."""
    await db.category.delete(category_id, application=x_application)
