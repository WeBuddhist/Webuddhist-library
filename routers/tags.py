from typing import Annotated

from fastapi import APIRouter, Path, status

from dependencies import ApplicationDep, DatabaseDep
from models.responses import IdResponse
from models.tag import TagInput, TagOutput
from search_updates import SearchUpdatesDep

router = APIRouter(prefix="/v2/tags", tags=["Tags"])


@router.get(
    "",
    summary="List tags",
    description="Retrieve all tags for an application.",
)
async def get_tags(db: DatabaseDep, x_application: ApplicationDep) -> list[TagOutput]:
    """List all tags for an application."""
    return await db.tag.get_all(application=x_application)


@router.post(
    "",
    status_code=201,
    summary="Create tag",
    description="Create a new tag for an application.",
)
async def create_tag(data: TagInput, db: DatabaseDep, x_application: ApplicationDep) -> IdResponse:
    """Create a new tag."""
    return IdResponse(id=await db.tag.create(data, application=x_application))


@router.delete(
    "/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete tag",
    description="Delete a tag from an application.",
)
async def delete_tag(
    search: SearchUpdatesDep,
    tag_id: Annotated[str, Path(description="The ID of the tag")],
    db: DatabaseDep,
    x_application: ApplicationDep,
) -> None:
    """Delete a tag."""
    for work_id in await db.tag.delete(tag_id, application=x_application):
        search.schedule("work", work_id)
