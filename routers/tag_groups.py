from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Path, status

from dependencies import RequiredAppHeader, get_api_key, get_db
from exceptions import DataNotFoundError
from models.responses import IdResponse
from models.tag import TagGroupInput, TagGroupOutput, TagGroupPatch

if TYPE_CHECKING:
    from database import Database

router = APIRouter(prefix="/v2/tag-groups", tags=["Tag Groups"])

GroupId = Annotated[str, Path(description="The ID of the tag group")]
TagId = Annotated[str, Path(description="The ID of the tag")]


async def _require_application(db: Database, application: str) -> None:
    if not await db.application.exists(application):
        raise DataNotFoundError(f"Application '{application}' not found")


@router.get(
    "",
    summary="List tag groups",
    description="Retrieve all tag groups for an application, each with the IDs of its tags.",
)
async def get_tag_groups(
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    x_application: RequiredAppHeader,
) -> list[TagGroupOutput]:
    await _require_application(db, x_application)
    return await db.tag_group.get_all(application=x_application)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create tag group",
    description="Create a tag group for an application.",
)
async def create_tag_group(
    data: TagGroupInput,
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    x_application: RequiredAppHeader,
) -> IdResponse:
    await _require_application(db, x_application)
    return IdResponse(id=await db.tag_group.create(data, application=x_application))


@router.patch(
    "/{group_id}",
    summary="Update tag group",
    description=(
        "Partially update a tag group. A sent `title` or `description` replaces that field as a whole: "
        "languages not sent are removed."
    ),
)
async def update_tag_group(
    group_id: GroupId,
    data: TagGroupPatch,
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    x_application: RequiredAppHeader,
) -> TagGroupOutput:
    await _require_application(db, x_application)
    return await db.tag_group.update(group_id, data, application=x_application)


@router.delete(
    "/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete tag group",
    description="Delete an empty tag group. Returns 409 while the group still has tags.",
)
async def delete_tag_group(
    group_id: GroupId,
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    x_application: RequiredAppHeader,
) -> None:
    await _require_application(db, x_application)
    await db.tag_group.delete(group_id, application=x_application)


@router.post(
    "/{group_id}/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Add tag to group",
    description="Put a tag in a group. A tag belongs to at most one group, so this moves it out of any other group.",
)
async def add_tag_to_group(
    group_id: GroupId,
    tag_id: TagId,
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    x_application: RequiredAppHeader,
) -> None:
    await _require_application(db, x_application)
    await db.tag_group.add_tag(group_id, tag_id, application=x_application)


@router.delete(
    "/{group_id}/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove tag from group",
    description="Take a tag out of a group. The tag itself is kept.",
)
async def remove_tag_from_group(
    group_id: GroupId,
    tag_id: TagId,
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    x_application: RequiredAppHeader,
) -> None:
    await _require_application(db, x_application)
    await db.tag_group.remove_tag(group_id, tag_id, application=x_application)
