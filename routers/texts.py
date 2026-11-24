import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from catalog_search import CatalogSearchService
from content_service import create_edition as publish_edition
from dependencies import DatabaseDep, OptionalAppHeader, StorageDep, get_catalog_search
from exceptions import ServiceUnavailableError
from models.audio import AudioInput, AudioOutput
from models.edition import EditionOutput
from models.requests import EditionRequestModel, EditionsQueryParams, TextFacetFilter, TextsQueryParams
from models.responses import IdResponse, PaginatedResponse, TextLanguageResponse
from models.text import TextInput, TextOutput, TextPatch
from search_updates import SearchUpdatesDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2/texts", tags=["Texts"])


@router.get(
    "",
    summary="List all texts",
    description="Retrieve a paginated list of texts (texts) with optional filters.",
)
async def get_all_texts(
    params: Annotated[TextsQueryParams, Query()],
    db: DatabaseDep,
    catalog_search: Annotated[CatalogSearchService | None, Depends(get_catalog_search)],
    x_application: OptionalAppHeader = None,
) -> PaginatedResponse[TextOutput]:
    """List all texts with optional filtering."""
    if params.title:
        if catalog_search is None:
            raise ServiceUnavailableError("Catalog search is required for text title search")
        texts = await catalog_search.search_texts(
            db=db,
            query=params.title,
            application=x_application,
            filters=params,
            offset=params.offset,
            limit=params.limit + 1,
        )
        return PaginatedResponse.from_items(texts, offset=params.offset, limit=params.limit)

    texts = await db.text.get_all(
        offset=params.offset,
        limit=params.limit + 1,
        filters=params,  # TextsQueryParams inherits from TextFilter, so this works
        application=x_application,
    )
    return PaginatedResponse.from_items(texts, offset=params.offset, limit=params.limit)


@router.get(
    "/languages",
    summary="List languages of texts",
    description="Count matching texts per language. Accepts the GET /v2/texts filters except language and title.",
)
async def get_text_languages(
    params: Annotated[TextFacetFilter, Query()],
    db: DatabaseDep,
) -> list[TextLanguageResponse]:
    """List the languages of texts matching the filters, with text counts."""
    return await db.text.get_languages(params)


@router.get(
    "/{text_id}",
    summary="Get a text",
    description="Retrieve a single text (text) by its ID.",
    responses={404: {"description": "Text not found"}},
)
async def get_text(
    text_id: Annotated[str, Path(description="The ID of the text to retrieve")],
    db: DatabaseDep,
    x_application: OptionalAppHeader = None,
) -> TextOutput:
    """Fetch a text (text) by its ID."""
    return await db.text.get(text_id=text_id, application=x_application)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a text",
    description="Create a new text (text) with the provided data.",
)
async def create_text(
    search: SearchUpdatesDep,
    data: TextInput,
    db: DatabaseDep,
    x_application: OptionalAppHeader = None,
) -> IdResponse:
    """Create a new text (text)."""
    text_id = await db.text.create(data, application=x_application)
    search.schedule("text", text_id)
    logger.info("Successfully created text with ID: %s", text_id)
    return IdResponse(id=text_id)


@router.get(
    "/{text_id}/editions",
    summary="List editions for a text",
    description="Retrieve all editions (editions) for a given text.",
)
async def get_editions(
    text_id: Annotated[str, Path(description="The ID of the text")],
    params: Annotated[EditionsQueryParams, Query()],
    db: DatabaseDep,
) -> list[EditionOutput]:
    """List all editions for a text."""
    return await db.edition.get_all(
        text_id=text_id,
        edition_type=params.edition_type,
    )


@router.post(
    "/{text_id}/editions",
    status_code=status.HTTP_201_CREATED,
    summary="Create an edition",
    description="Create a new edition (edition) for a text.",
)
async def create_edition(
    search: SearchUpdatesDep,
    text_id: Annotated[str, Path(description="The ID of the text")],
    data: EditionRequestModel,
    db: DatabaseDep,
    storage: StorageDep,
) -> IdResponse:
    """Create a new edition for a text."""
    edition_id = await publish_edition(db, storage, text_id, data)
    search.schedule("edition", edition_id)
    return IdResponse(id=edition_id)


@router.get(
    "/{text_id}/audios",
    summary="List audios for a text",
    description="Retrieve every audio (performance) that recites a text.",
    response_model_exclude_none=True,
)
async def get_audios(
    text_id: Annotated[str, Path(description="The ID of the text")],
    db: DatabaseDep,
) -> list[AudioOutput]:
    return await db.audio.get_all(text_id)


@router.post(
    "/{text_id}/audios",
    status_code=status.HTTP_201_CREATED,
    summary="Create an audio",
    description=(
        "Create an audio (one performance) that recites a text. It belongs to the text's Work. "
        "Upload its recordings with POST /v2/audios/{audio_id}/recordings."
    ),
)
async def create_audio(
    text_id: Annotated[str, Path(description="The ID of the recited text")],
    data: AudioInput,
    db: DatabaseDep,
) -> IdResponse:
    logger.info("Creating audio for text %s", text_id)
    return IdResponse(id=await db.audio.create(text_id, data))


@router.patch(
    "/{text_id}",
    summary="Update a text",
    description="Partially update a text with the provided data.",
)
async def update_text(
    search: SearchUpdatesDep,
    text_id: Annotated[str, Path(description="The ID of the text to update")],
    data: TextPatch,
    db: DatabaseDep,
    x_application: OptionalAppHeader = None,
) -> TextOutput:
    """Update a text."""
    logger.info("Updating text %s with: %s", text_id, data.model_dump_json())
    text = await db.text.update(text_id, data, application=x_application)
    search.schedule("text", text_id)
    return text


@router.delete(
    "/{text_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete text",
    description="Delete a text if it has no editions, audios or incoming text relationships.",
)
async def delete_text(
    search: SearchUpdatesDep,
    text_id: Annotated[str, Path(description="The ID of the text to delete")],
    db: DatabaseDep,
) -> None:
    """Delete a text."""
    await db.text.delete(text_id)
    search.schedule("text", text_id)


@router.post(
    "/{text_id}/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Add tag to text",
    description="Add a tag to a text.",
)
async def tag_text(
    search: SearchUpdatesDep,
    text_id: Annotated[str, Path(description="The ID of the text")],
    tag_id: Annotated[str, Path(description="The ID of the tag to add")],
    db: DatabaseDep,
    x_application: OptionalAppHeader = None,
) -> None:
    """Add a tag to a text."""
    work_id = await db.text.get_work_id(text_id)
    await db.tag.tag_work(work_id, tag_id, application=x_application)
    search.schedule("work", work_id)


@router.delete(
    "/{text_id}/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove tag from text",
    description="Remove a tag from a text.",
)
async def untag_text(
    search: SearchUpdatesDep,
    text_id: Annotated[str, Path(description="The ID of the text")],
    tag_id: Annotated[str, Path(description="The ID of the tag to remove")],
    db: DatabaseDep,
    x_application: OptionalAppHeader = None,
) -> None:
    """Remove a tag from a text."""
    work_id = await db.text.get_work_id(text_id)
    await db.tag.untag_work(work_id, tag_id, application=x_application)
    search.schedule("work", work_id)
