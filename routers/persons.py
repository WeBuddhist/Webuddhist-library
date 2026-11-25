from typing import Annotated

from fastapi import APIRouter, Query, status

from database import person as person_database
from dependencies import CatalogSearchDep, DatabaseDep
from errors import ServiceUnavailableError
from models.person import PersonInput, PersonOutput, PersonPatch
from models.requests import PersonsQueryParams
from models.responses import IdResponse, PaginatedResponse
from routers.params import PersonId
from search.updates import SearchUpdatesDep

router = APIRouter(prefix="/v2/persons", tags=["Persons"])


@router.get("/{person_id}", summary="Get a person", description="Retrieve a person by their ID.")
async def get_person(person_id: PersonId, db: DatabaseDep) -> PersonOutput:
    return await person_database.get(db, person_id)


@router.get("", summary="List all persons", description="Retrieve a paginated list of persons.")
async def get_all_persons(
    params: Annotated[PersonsQueryParams, Query()], db: DatabaseDep, catalog_search: CatalogSearchDep
) -> PaginatedResponse[PersonOutput]:
    page = {"filters": params, "offset": params.offset, "limit": params.limit + 1}
    if not params.name:
        persons = await person_database.get_all(db, **page)
    elif catalog_search is None:
        raise ServiceUnavailableError("Catalog search is required for person name search")
    else:
        persons = await catalog_search.search_persons(db=db, query=params.name, **page)
    return PaginatedResponse.from_items(persons, offset=params.offset, limit=params.limit)


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create a person", description="Create a new person.")
async def create_person(search: SearchUpdatesDep, data: PersonInput, db: DatabaseDep) -> IdResponse:
    person_id = await person_database.create(db, data)
    search.schedule("person", person_id)
    return IdResponse(id=person_id)


@router.patch("/{person_id}", summary="Update a person", description="Partially update a person.")
async def update_person(
    search: SearchUpdatesDep, person_id: PersonId, data: PersonPatch, db: DatabaseDep
) -> PersonOutput:
    person = await person_database.update(db, person_id, data)
    search.schedule("person", person_id)
    return person


@router.delete(
    "/{person_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete person",
    description="Delete a person if they are not referenced by contributions.",
)
async def delete_person(search: SearchUpdatesDep, person_id: PersonId, db: DatabaseDep) -> None:
    await person_database.delete(db, person_id)
    search.schedule("person", person_id)
