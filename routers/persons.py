import logging
from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from dependencies import CatalogSearchDep, DatabaseDep
from exceptions import ServiceUnavailableError
from models.person import PersonInput, PersonOutput, PersonPatch
from models.requests import PersonsQueryParams
from models.responses import IdResponse, PaginatedResponse
from search_updates import SearchUpdatesDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2/persons", tags=["Persons"])


@router.get(
    "/{person_id}",
    summary="Get a person",
    description="Retrieve a person by their ID.",
)
async def get_person(
    person_id: Annotated[str, Path(description="The ID of the person")],
    db: DatabaseDep,
) -> PersonOutput:
    """Fetch a person by ID."""
    return await db.person.get(person_id)


@router.get(
    "",
    summary="List all persons",
    description="Retrieve a paginated list of persons.",
)
async def get_all_persons(
    params: Annotated[PersonsQueryParams, Query()],
    db: DatabaseDep,
    catalog_search: CatalogSearchDep,
) -> PaginatedResponse[PersonOutput]:
    """List all persons with optional filtering."""
    page = {"filters": params, "offset": params.offset, "limit": params.limit + 1}
    if not params.name:
        persons = await db.person.get_all(**page)
    elif catalog_search is None:
        raise ServiceUnavailableError("Catalog search is required for person name search")
    else:
        persons = await catalog_search.search_persons(db=db, query=params.name, **page)
    return PaginatedResponse.from_items(persons, offset=params.offset, limit=params.limit)


@router.post(
    "",
    status_code=201,
    summary="Create a person",
    description="Create a new person.",
)
async def create_person(
    search: SearchUpdatesDep,
    data: PersonInput,
    db: DatabaseDep,
) -> IdResponse:
    """Create a new person."""
    logger.info("Creating person: %s", data.model_dump_json())
    person_id = await db.person.create(data)
    search.schedule("person", person_id)
    return IdResponse(id=person_id)


@router.patch(
    "/{person_id}",
    summary="Update a person",
    description="Partially update a person.",
)
async def update_person(
    search: SearchUpdatesDep,
    person_id: Annotated[str, Path(description="The ID of the person")],
    data: PersonPatch,
    db: DatabaseDep,
) -> PersonOutput:
    """Update a person."""
    logger.info("Updating person %s with: %s", person_id, data.model_dump_json())
    person = await db.person.update(person_id, data)
    search.schedule("person", person_id)
    return person


@router.delete(
    "/{person_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete person",
    description="Delete a person if they are not referenced by contributions.",
)
async def delete_person(
    search: SearchUpdatesDep,
    person_id: Annotated[str, Path(description="The ID of the person")],
    db: DatabaseDep,
) -> None:
    """Delete a person."""
    await db.person.delete(person_id)
    search.schedule("person", person_id)
