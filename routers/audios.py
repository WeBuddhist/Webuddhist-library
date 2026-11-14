import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, File, Form, Path, UploadFile, status

from content_service import create_recording
from dependencies import get_api_key, get_db, get_storage
from models.audio import AudioOutput, AudioPatch
from models.recording import RecordingInput, RecordingOutput
from models.responses import IdResponse

if TYPE_CHECKING:
    from database import Database
    from storage import Storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2/audios", tags=["Audios"])


@router.get(
    "/{audio_id}",
    summary="Get an audio",
    description="Retrieve one performance of a text, with its contributors and recordings.",
    response_model_exclude_none=True,
)
async def get_audio(
    audio_id: Annotated[str, Path(description="The ID of the audio")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> AudioOutput:
    return await db.audio.get(audio_id)


@router.patch(
    "/{audio_id}",
    summary="Update an audio",
    description="Partially update an audio's metadata. The recited text cannot change.",
    response_model_exclude_none=True,
)
async def update_audio(
    audio_id: Annotated[str, Path(description="The ID of the audio")],
    data: AudioPatch,
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> AudioOutput:
    logger.info("Updating audio %s with: %s", audio_id, data.model_dump_json())
    return await db.audio.update(audio_id, data)


@router.delete(
    "/{audio_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete audio",
    description="Delete an audio that has no recordings.",
)
async def delete_audio(
    audio_id: Annotated[str, Path(description="The ID of the audio")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> None:
    await db.audio.delete(audio_id)


@router.get(
    "/{audio_id}/recordings",
    summary="Get audio recordings",
    description="Retrieve every recording (version) of an audio.",
    response_model_exclude_none=True,
)
async def get_recordings(
    audio_id: Annotated[str, Path(description="The ID of the audio")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
) -> list[RecordingOutput]:
    return await db.recording.get_all_by_audio(audio_id)


@router.post(
    "/{audio_id}/recordings",
    status_code=status.HTTP_201_CREATED,
    summary="Add audio recording",
    description="Upload one recording of an audio, with its metadata as a JSON form field.",
)
async def post_recording(
    audio_id: Annotated[str, Path(description="The ID of the audio")],
    metadata: Annotated[str, Form(description="JSON-encoded recording metadata")],
    file: Annotated[UploadFile, File(description="Audio file")],
    _api_key: Annotated[str, Depends(get_api_key)],
    db: Annotated[Database, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> IdResponse:
    data = RecordingInput.model_validate_json(metadata)
    return IdResponse(id=await create_recording(db, storage, audio_id, data, file))
