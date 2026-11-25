from fastapi import APIRouter, status

from database import tag
from dependencies import ApplicationDep, DatabaseDep
from models.responses import IdResponse
from models.tag import TagInput, TagOutput
from routers.params import TagId
from search.updates import SearchUpdatesDep

router = APIRouter(prefix="/v2/tags", tags=["Tags"])


@router.get("", summary="List tags", description="Retrieve all tags for an application.")
async def get_tags(db: DatabaseDep, x_application: ApplicationDep) -> list[TagOutput]:
    return await tag.get_all(db, application=x_application)


@router.post(
    "", status_code=status.HTTP_201_CREATED, summary="Create tag", description="Create a new tag for an application."
)
async def create_tag(data: TagInput, db: DatabaseDep, x_application: ApplicationDep) -> IdResponse:
    return IdResponse(id=await tag.create(db, data, application=x_application))


@router.delete(
    "/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete tag",
    description="Delete a tag from an application.",
)
async def delete_tag(search: SearchUpdatesDep, tag_id: TagId, db: DatabaseDep, x_application: ApplicationDep) -> None:
    for work_id in await tag.delete(db, tag_id, application=x_application):
        search.schedule("work", work_id)
