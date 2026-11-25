from typing import Annotated

from fastapi import APIRouter, Query, status

from database import category
from dependencies import ApplicationDep, DatabaseDep
from models.category import CategoryInput, CategoryOutput, CategoryPatch
from models.responses import IdResponse
from routers.params import CategoryId

router = APIRouter(prefix="/v2/categories", tags=["Categories"])


@router.get("", summary="List categories", description="Retrieve categories for an application.")
async def get_categories(
    db: DatabaseDep,
    x_application: ApplicationDep,
    parent_id: Annotated[str | None, Query(description="Filter by parent category ID")] = None,
) -> list[CategoryOutput]:
    return await category.get_all(db, application=x_application, parent_id=parent_id)


@router.get(
    "/{category_id}",
    summary="Get category by ID",
    description="Retrieve a single category by its ID for an application.",
)
async def get_category_by_id(category_id: CategoryId, db: DatabaseDep, x_application: ApplicationDep) -> CategoryOutput:
    return await category.get(db, category_id, application=x_application)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create category",
    description="Create a new category for an application.",
)
async def create_category(data: CategoryInput, db: DatabaseDep, x_application: ApplicationDep) -> IdResponse:
    return IdResponse(id=await category.create(db, data, application=x_application))


@router.patch(
    "/{category_id}",
    summary="Update category localizations",
    description=(
        "Add or replace title and description localizations of a category. Languages sent are added or "
        "replaced; languages not sent are kept."
    ),
)
async def update_category(
    category_id: CategoryId, data: CategoryPatch, db: DatabaseDep, x_application: ApplicationDep
) -> CategoryOutput:
    return await category.update(db, category_id, data, application=x_application)


@router.delete(
    "/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete category",
    description="Delete a category and its child categories for an application.",
)
async def delete_category(category_id: CategoryId, db: DatabaseDep, x_application: ApplicationDep) -> None:
    await category.delete(db, category_id, application=x_application)
