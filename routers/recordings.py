import logging
from typing import Annotated

from fastapi import APIRouter, Body, Path, status
from fastapi.responses import RedirectResponse

from content_service import sign_recording_audio_url
from dependencies import DatabaseDep, StorageDep
from models.recording import RecordingAudioUrl, RecordingCue, RecordingCues, RecordingOutput, RecordingPatch
from storage import PRESIGNED_URL_TTL_SECONDS

logger = logging.getLogger(__name__)

RecordingId = Annotated[str, Path(description="The ID of the recording")]

router = APIRouter(prefix="/v2/recordings", tags=["Recordings"])


@router.get(
    "/{recording_id}",
    summary="Get a recording",
    description="Retrieve metadata for an audio recording.",
    response_model_exclude_none=True,
)
async def get_recording(recording_id: RecordingId, db: DatabaseDep) -> RecordingOutput:
    """Fetch a recording by ID."""
    return await db.recording.get(recording_id)


@router.get(
    "/{recording_id}/audio",
    status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    summary="Get recording audio",
    description="Redirect to a short-lived presigned URL for the recording's audio file.",
    response_class=RedirectResponse,
)
async def get_recording_audio(
    recording_id: RecordingId,
    db: DatabaseDep,
    storage: StorageDep,
) -> RedirectResponse:
    """Redirect to the recording's audio in storage, which serves range requests directly."""
    url = await sign_recording_audio_url(db, storage, recording_id)
    return RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get(
    "/{recording_id}/audio-url",
    summary="Get a playable link to recording audio",
    description=(
        "Return a short-lived presigned URL for the recording's audio file. Players such as an HTML "
        "`<audio>` tag can't send `X-API-Key`, so fetch this link with the key and give the URL to the player."
    ),
)
async def get_recording_audio_url(
    recording_id: RecordingId,
    db: DatabaseDep,
    storage: StorageDep,
) -> RecordingAudioUrl:
    url = await sign_recording_audio_url(db, storage, recording_id)
    return RecordingAudioUrl(url=url, expires_in=PRESIGNED_URL_TTL_SECONDS)


@router.get(
    "/{recording_id}/cues",
    summary="Get recording cues",
    description="Retrieve every cue of a recording, ordered by start time.",
)
async def get_recording_cues(recording_id: RecordingId, db: DatabaseDep) -> list[RecordingCue]:
    return await db.recording.get_cues(recording_id)


@router.put(
    "/{recording_id}/cues",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Replace recording cues",
    description="Replace all cues of a recording.",
)
async def replace_recording_cues(
    recording_id: RecordingId,
    data: Annotated[RecordingCues, Body(min_length=1)],
    db: DatabaseDep,
) -> None:
    logger.info("Replacing %d cues for recording %s", len(data), recording_id)
    await db.recording.replace_cues(recording_id, data)


@router.delete(
    "/{recording_id}/cues",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete recording cues",
    description="Delete all cues of a recording.",
)
async def delete_recording_cues(recording_id: RecordingId, db: DatabaseDep) -> None:
    await db.recording.delete_cues(recording_id)


@router.put(
    "/{recording_id}/edition/{edition_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Follow an edition",
    description=(
        "Link a recording to the edition its cues point into. The edition must be an edition of the text the "
        "audio recites. Following a different edition deletes the existing cues."
    ),
)
async def set_recording_edition(
    recording_id: RecordingId,
    edition_id: Annotated[str, Path(description="The ID of the edition to follow")],
    db: DatabaseDep,
) -> None:
    await db.recording.set_follows(recording_id, edition_id)


@router.delete(
    "/{recording_id}/edition",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Stop following an edition",
    description="Remove the recording's link to its edition and delete its cues.",
)
async def delete_recording_edition(recording_id: RecordingId, db: DatabaseDep) -> None:
    await db.recording.remove_follows(recording_id)


@router.patch(
    "/{recording_id}",
    summary="Update a recording",
    description="Partially update a recording's version name or duration.",
    response_model_exclude_none=True,
)
async def update_recording(
    recording_id: RecordingId,
    data: RecordingPatch,
    db: DatabaseDep,
) -> RecordingOutput:
    """Update a recording's metadata. The audio file itself is replaced by deleting and re-uploading."""
    logger.info("Updating recording %s with: %s", recording_id, data.model_dump_json())
    return await db.recording.update(recording_id, data)


@router.delete(
    "/{recording_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete recording",
    description="Delete a recording's metadata. Its audio remains in storage.",
)
async def delete_recording(recording_id: RecordingId, db: DatabaseDep) -> None:
    """Delete a recording."""
    await db.recording.delete(recording_id)
