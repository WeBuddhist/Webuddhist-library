from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile, status

from content_service import create_recording
from database import audio, recording
from dependencies import DatabaseDep, StorageDep
from models.audio import AudioOutput, AudioPatch
from models.recording import RecordingInput, RecordingOutput
from models.responses import IdResponse
from routers.params import AudioId

router = APIRouter(prefix="/v2/audios", tags=["Audios"])


@router.get(
    "/{audio_id}",
    summary="Get an audio",
    description="Retrieve one performance of a text, with its contributors and recordings.",
    response_model_exclude_none=True,
)
async def get_audio(audio_id: AudioId, db: DatabaseDep) -> AudioOutput:
    return await audio.get(db, audio_id)


@router.patch(
    "/{audio_id}",
    summary="Update an audio",
    description="Partially update an audio's metadata. The recited text cannot change.",
    response_model_exclude_none=True,
)
async def update_audio(audio_id: AudioId, data: AudioPatch, db: DatabaseDep) -> AudioOutput:
    return await audio.update(db, audio_id, data)


@router.delete(
    "/{audio_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete audio",
    description="Delete an audio that has no recordings.",
)
async def delete_audio(audio_id: AudioId, db: DatabaseDep) -> None:
    await audio.delete(db, audio_id)


@router.get(
    "/{audio_id}/recordings",
    summary="Get audio recordings",
    description="Retrieve every recording (version) of an audio.",
    response_model_exclude_none=True,
)
async def get_recordings(audio_id: AudioId, db: DatabaseDep) -> list[RecordingOutput]:
    return await recording.get_all_by_audio(db, audio_id)


@router.post(
    "/{audio_id}/recordings",
    status_code=status.HTTP_201_CREATED,
    summary="Add audio recording",
    description="Upload one recording of an audio, with its metadata as a JSON form field.",
)
async def post_recording(
    audio_id: AudioId,
    metadata: Annotated[str, Form(description="JSON-encoded recording metadata")],
    file: Annotated[UploadFile, File(description="Audio file")],
    db: DatabaseDep,
    storage: StorageDep,
) -> IdResponse:
    data = RecordingInput.model_validate_json(metadata)
    return IdResponse(id=await create_recording(db, storage, audio_id, data, file))
