import logging
from typing import Annotated

from fastapi import APIRouter, Body, status
from fastapi.responses import RedirectResponse

from content_service import sign_recording_audio_url
from database import recording
from dependencies import DatabaseDep, StorageDep
from models.recording import RecordingAudioUrl, RecordingCue, RecordingCues, RecordingOutput, RecordingPatch
from routers.params import RecordingId
from storage import PRESIGNED_URL_TTL_SECONDS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v2/recordings", tags=["Recordings"])


@router.get(
    "/{recording_id}",
    summary="Get a recording",
    description="Retrieve metadata for an audio recording.",
    response_model_exclude_none=True,
)
async def get_recording(recording_id: RecordingId, db: DatabaseDep) -> RecordingOutput:
    return await recording.get(db, recording_id)


@router.get(
    "/{recording_id}/audio",
    status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    summary="Get recording audio",
    description="Redirect to a short-lived presigned URL for the recording's audio file.",
    response_class=RedirectResponse,
)
async def get_recording_audio(recording_id: RecordingId, db: DatabaseDep, storage: StorageDep) -> RedirectResponse:
    url = await sign_recording_audio_url(db, storage, recording_id)
    return RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get(
    "/{recording_id}/audio-url",
    summary="Get a playable link to recording audio",
    description=(
        "Return a short-lived presigned URL for the recording's audio file. Players such as an HTML `<audio>` "
        "tag can't send `X-API-Key`, so fetch this link with the key and give the URL to the player."
    ),
)
async def get_recording_audio_url(recording_id: RecordingId, db: DatabaseDep, storage: StorageDep) -> RecordingAudioUrl:
    url = await sign_recording_audio_url(db, storage, recording_id)
    return RecordingAudioUrl(url=url, expires_in=PRESIGNED_URL_TTL_SECONDS)


@router.get(
    "/{recording_id}/cues",
    summary="Get recording cues",
    description="Retrieve every cue of a recording, ordered by start time.",
)
async def get_recording_cues(recording_id: RecordingId, db: DatabaseDep) -> list[RecordingCue]:
    return await recording.get_cues(db, recording_id)


@router.put(
    "/{recording_id}/cues",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Replace recording cues",
    description="Replace all cues of a recording.",
)
async def replace_recording_cues(
    recording_id: RecordingId, data: Annotated[RecordingCues, Body(min_length=1)], db: DatabaseDep
) -> None:
    logger.info("Replacing %d cues for recording %s", len(data), recording_id)
    await recording.replace_cues(db, recording_id, data)


@router.delete(
    "/{recording_id}/cues",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete recording cues",
    description="Delete all cues of a recording.",
)
async def delete_recording_cues(recording_id: RecordingId, db: DatabaseDep) -> None:
    await recording.delete_cues(db, recording_id)


@router.patch(
    "/{recording_id}",
    summary="Update a recording",
    description="Partially update a recording's version name or duration.",
    response_model_exclude_none=True,
)
async def update_recording(recording_id: RecordingId, data: RecordingPatch, db: DatabaseDep) -> RecordingOutput:
    return await recording.update(db, recording_id, data)


@router.delete(
    "/{recording_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete recording",
    description="Delete a recording's metadata. Its audio remains in storage.",
)
async def delete_recording(recording_id: RecordingId, db: DatabaseDep) -> None:
    await recording.delete(db, recording_id)
