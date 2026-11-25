import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Path, status

from database import application
from dependencies import DatabaseDep, get_admin_key
from models.requests import ApplicationCreateRequest
from models.responses import ApplicationResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v2/applications", tags=["Applications"], dependencies=[Depends(get_admin_key)])


@router.post(
    "", status_code=status.HTTP_201_CREATED, summary="Create application", description="Create a new application."
)
async def create_application(data: ApplicationCreateRequest, db: DatabaseDep) -> ApplicationResponse:
    app_id = app_name = data.name.strip().lower()
    created_id = await application.create(db, application_id=app_id, name=app_name)
    return ApplicationResponse(id=created_id, name=app_name)


@router.delete(
    "/{application_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete application",
    description="Delete an unused application.",
)
async def delete_application(
    application_id: Annotated[str, Path(description="The ID of the application")], db: DatabaseDep
) -> None:
    await application.delete(db, application_id)
