import logging
from typing import Annotated

from fastapi import APIRouter, Query, status

from content_service import create_edition as publish_edition
from database import audio, edition, tag
from database import text as text_database
from dependencies import CatalogSearchDep, DatabaseDep, OptionalAppHeader, StorageDep
from errors import ServiceUnavailableError
from models.audio import AudioInput, AudioOutput
from models.edition import EditionOutput
from models.requests import EditionRequestModel, EditionsQueryParams, TextFacetFilter, TextsQueryParams
from models.responses import IdResponse, PaginatedResponse, TextLanguageResponse
from models.text import TextInput, TextOutput, TextPatch
from routers.params import TagId, TextId
from search.updates import SearchUpdatesDep

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v2/texts", tags=["Texts"])


@router.get(
    "", summary="List all texts", description="Retrieve a paginated list of texts (texts) with optional filters."
)
async def get_all_texts(
    params: Annotated[TextsQueryParams, Query()],
    db: DatabaseDep,
    catalog_search: CatalogSearchDep,
    x_application: OptionalAppHeader = None,
) -> PaginatedResponse[TextOutput]:
    page = {"filters": params, "application": x_application, "offset": params.offset, "limit": params.limit + 1}
    if not params.title:
        texts = await text_database.get_all(db, **page)
    elif catalog_search is None:
        raise ServiceUnavailableError("Catalog search is required for text title search")
    else:
        texts = await catalog_search.search_texts(db=db, query=params.title, **page)
    return PaginatedResponse.from_items(texts, offset=params.offset, limit=params.limit)


@router.get(
    "/languages",
    summary="List languages of texts",
    description="Count matching texts per language. Accepts the GET /v2/texts filters except language and title.",
)
async def get_text_languages(
    params: Annotated[TextFacetFilter, Query()], db: DatabaseDep
) -> list[TextLanguageResponse]:
    return await text_database.get_languages(db, params)


@router.get(
    "/{text_id}",
    summary="Get a text",
    description="Retrieve a single text (text) by its ID.",
    responses={404: {"description": "Text not found"}},
)
async def get_text(text_id: TextId, db: DatabaseDep, x_application: OptionalAppHeader = None) -> TextOutput:
    return await text_database.get(db, text_id=text_id, application=x_application)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a text",
    description="Create a new text (text) with the provided data.",
)
async def create_text(
    search: SearchUpdatesDep, data: TextInput, db: DatabaseDep, x_application: OptionalAppHeader = None
) -> IdResponse:
    text_id = await text_database.create(db, data, application=x_application)
    search.schedule("text", text_id)
    logger.info("Successfully created text with ID: %s", text_id)
    return IdResponse(id=text_id)


@router.get(
    "/{text_id}/editions",
    summary="List editions for a text",
    description="Retrieve all editions (editions) for a given text.",
)
async def get_editions(
    text_id: TextId, params: Annotated[EditionsQueryParams, Query()], db: DatabaseDep
) -> list[EditionOutput]:
    return await edition.get_all(db, text_id=text_id, edition_type=params.edition_type)


@router.post(
    "/{text_id}/editions",
    status_code=status.HTTP_201_CREATED,
    summary="Create an edition",
    description="Create a new edition (edition) for a text.",
)
async def create_edition(
    search: SearchUpdatesDep, text_id: TextId, data: EditionRequestModel, db: DatabaseDep, storage: StorageDep
) -> IdResponse:
    edition_id = await publish_edition(db, storage, text_id, data)
    search.schedule("edition", edition_id)
    return IdResponse(id=edition_id)


@router.get(
    "/{text_id}/audios",
    summary="List audios for a text",
    description="Retrieve every audio (performance) that recites a text.",
    response_model_exclude_none=True,
)
async def get_audios(text_id: TextId, db: DatabaseDep) -> list[AudioOutput]:
    return await audio.get_all(db, text_id)


@router.post(
    "/{text_id}/audios",
    status_code=status.HTTP_201_CREATED,
    summary="Create an audio",
    description=(
        "Create an audio (one performance) that recites a text. It belongs to the text's Work. Upload its "
        "recordings with POST /v2/audios/{audio_id}/recordings."
    ),
)
async def create_audio(text_id: TextId, data: AudioInput, db: DatabaseDep) -> IdResponse:
    logger.info("Creating audio for text %s", text_id)
    return IdResponse(id=await audio.create(db, text_id, data))


@router.patch("/{text_id}", summary="Update a text", description="Partially update a text with the provided data.")
async def update_text(
    search: SearchUpdatesDep, text_id: TextId, data: TextPatch, db: DatabaseDep, x_application: OptionalAppHeader = None
) -> TextOutput:
    text = await text_database.update(db, text_id, data, application=x_application)
    search.schedule("text", text_id)
    return text


@router.delete(
    "/{text_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete text",
    description="Delete a text if it has no editions, audios or incoming text relationships.",
)
async def delete_text(search: SearchUpdatesDep, text_id: TextId, db: DatabaseDep) -> None:
    await text_database.delete(db, text_id)
    search.schedule("text", text_id)


@router.post(
    "/{text_id}/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Add tag to text",
    description="Add a tag to a text.",
)
async def tag_text(
    search: SearchUpdatesDep, text_id: TextId, tag_id: TagId, db: DatabaseDep, x_application: OptionalAppHeader = None
) -> None:
    work_id = await text_database.get_work_id(db, text_id)
    await tag.tag_work(db, work_id, tag_id, application=x_application)
    search.schedule("work", work_id)


@router.delete(
    "/{text_id}/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove tag from text",
    description="Remove a tag from a text.",
)
async def untag_text(
    search: SearchUpdatesDep, text_id: TextId, tag_id: TagId, db: DatabaseDep, x_application: OptionalAppHeader = None
) -> None:
    work_id = await text_database.get_work_id(db, text_id)
    await tag.untag_work(db, work_id, tag_id, application=x_application)
    search.schedule("work", work_id)
