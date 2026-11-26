"""Immutable uploads precede a single graph transaction; rollback keeps the previous content active."""

from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING

from database import edition, excerpt, recording, segment
from database.content_state import read_at_revision, read_state
from errors import DataValidationError
from identifier import generate_id
from models.content_operation import ContentOperation, DeleteOperation, InsertOperation
from models.enums import AudioFormat
from storage import PRESIGNED_URL_TTL_SECONDS, Storage, edition_key, recording_key

if TYPE_CHECKING:
    from fastapi import UploadFile

    from database import Database
    from models.recording import RecordingInput
    from models.requests import EditionRequestModel


async def create_edition(db: Database, storage: Storage, text_id: str, data: EditionRequestModel) -> str:
    edition_id = generate_id()
    if data.content is None:
        length = sum(span.end - span.start for span in data.excerpt_of.spans) if data.excerpt_of else 0
        return await edition.create(db, data.metadata, edition_id, text_id, length, excerpt_of=data.excerpt_of)
    key = edition_key(text_id, edition_id)
    await storage.put_immutable(key, data.content.encode(), "text/plain; charset=utf-8")
    return await edition.create(
        db,
        data.metadata,
        edition_id,
        text_id,
        len(data.content),
        segmentation=data.segmentation,
        pagination=data.pagination,
        content_key=key,
    )


async def read_content(
    db: Database, storage: Storage, edition_id: str, *, span_start: int | None = None, span_end: int | None = None
) -> str:
    """Slice edition content when both bounds are supplied, after assembling an excerpt in reading order."""
    state, slices = await db.read(read_at_revision, edition_id, partial(excerpt.get_slices, edition_id=edition_id))
    if state.is_excerpt:
        source = await storage.read_text(slices[0].key)
        content = "".join(source[piece.start : piece.end] for piece in slices)
    else:
        content = await storage.read_text(state.stored_key)
    if span_start is not None and span_end is not None:
        return content[span_start:span_end]
    return content


async def read_segment_content(db: Database, storage: Storage, segment_id: str, application: str | None) -> str:
    key, span = await segment.get_content_location(db, segment_id, application)
    content = await storage.read_text(key)
    return content[span.start : span.end]


async def sign_edition_content_url(db: Database, storage: Storage, edition_id: str) -> tuple[str, datetime]:
    state = await db.read(read_state, edition_id)
    expires_at = datetime.now(UTC) + timedelta(seconds=PRESIGNED_URL_TTL_SECONDS)
    url = await storage.generate_url(state.stored_key)
    return (url, expires_at)


async def sign_recording_audio_url(db: Database, storage: Storage, recording_id: str) -> str:
    key = await recording.get_storage_location(db, recording_id)
    return await storage.generate_url(key)


async def edit_content(db: Database, storage: Storage, edition_id: str, data: ContentOperation) -> None:
    state = await db.read(read_state, edition_id)
    edit = data.operation
    start, end = (edit.position, edit.position) if isinstance(edit, InsertOperation) else (edit.start, edit.end)
    replacement = "" if isinstance(edit, DeleteOperation) else edit.text
    state.validate_span(end)
    if state.length + len(replacement) - (end - start) <= 0:
        raise DataValidationError("Content edits must leave at least one character")
    content = await storage.read_text(state.stored_key)
    if len(content) != state.length:
        raise DataValidationError("Stored content length differs from edition metadata")
    updated = content[:start] + replacement + content[end:]
    span_edit = (start, end, len(replacement)) if updated != content else None
    key = edition_key(state.text_id, edition_id, generate_id())
    await storage.put_immutable(key, updated.encode(), "text/plain; charset=utf-8")

    await edition.commit_content_edit(db, state, content_key=key, content_length=len(updated), span_edit=span_edit)


async def create_recording(
    db: Database, storage: Storage, audio_id: str, metadata: RecordingInput, file: UploadFile
) -> str:
    audio_format = AudioFormat.from_content_type(file.content_type)
    if audio_format is None:
        raise DataValidationError(f"Unsupported audio content type '{file.content_type}'")
    size = file.size
    if not size:
        raise DataValidationError("Audio file is empty or its size is unavailable")
    await recording.validate_target(db, audio_id, metadata.edition_id)
    await file.seek(0)
    recording_id = generate_id()
    key = recording_key(recording_id, audio_format)
    await storage.put_immutable(key, file.file, audio_format.content_type)
    return await recording.add(
        db, audio_id, metadata, recording_id, audio_key=key, audio_format=audio_format, size_bytes=size
    )
