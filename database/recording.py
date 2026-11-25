from typing import TYPE_CHECKING, LiteralString

from pydantic import MISSING

from errors import DataNotFoundError, DataValidationError
from models.enums import AudioFormat
from models.recording import RecordingCue, RecordingInput, RecordingOutput, RecordingPatch

from . import nomen
from .content_state import ContentState, advance_revision, read_state, read_value, validate_edition_exists
from .locking import lock_nodes
from .repository import (
    SessionSource,
    create_id,
    fetch_all,
    fetch_one,
    fetch_record,
    query_record,
    reading,
    require_node,
    set_properties,
    writing,
)

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction

_RECORDING_RETURN: LiteralString = f"""
RETURN {{
    id: r.id, format: r.format, size_bytes: r.size_bytes, duration_ms: r.duration_ms,
    audio_id: a.id,
    text_id: [(a)-[:RECITATION_OF]->(t:Text) | t.id][0],
    edition_id: [(r)-[:FOLLOWS]->(m:Edition) | m.id][0],
    title: {nomen.optional_localized_map("(r)-[:HAS_TITLE]->(:Nomen)")}
}} AS recording
"""
GET_BY_ID_QUERY: LiteralString = f"""
MATCH (r:Recording {{id: $recording_id}})-[:RENDITION_OF]->(a:Audio)
{_RECORDING_RETURN}
"""
GET_BY_AUDIO_ID_QUERY: LiteralString = f"""
MATCH (r:Recording)-[:RENDITION_OF]->(a:Audio {{id: $audio_id}})
WITH r, a
ORDER BY r.id
{_RECORDING_RETURN}
"""
GET_BY_EDITION_ID_QUERY: LiteralString = f"""
MATCH (r:Recording)-[:FOLLOWS]->(:Edition {{id: $edition_id}})
MATCH (r)-[:RENDITION_OF]->(a:Audio)
WITH r, a
ORDER BY r.id
{_RECORDING_RETURN}
"""
FOLLOWS_TARGET_QUERY: LiteralString = """
MATCH (a:Audio {id: $audio_id})-[:RECITATION_OF]->(recited:Text)
MATCH (m:Edition {id: $edition_id})-[:EDITION_OF]->(edition_text:Text)
RETURN recited.id AS recited_text_id, edition_text.id AS edition_text_id
"""
LINKS_QUERY: LiteralString = """
MATCH (r:Recording {id: $recording_id})-[:RENDITION_OF]->(a:Audio)
RETURN a.id AS audio_id, [(r)-[:FOLLOWS]->(m:Edition) | m.id][0] AS edition_id
"""
CREATE_QUERY: LiteralString = """
MATCH (a:Audio {id: $audio_id})
OPTIONAL MATCH (title:Nomen {id: $title_nomen_id})
CREATE (r:Recording {
    id: $recording_id, audio_key: $audio_key, format: $format, size_bytes: $size_bytes,
    duration_ms: $duration_ms
})
CREATE (r)-[:RENDITION_OF]->(a)
CALL (*) { WHEN title IS NOT NULL THEN { CREATE (r)-[:HAS_TITLE]->(title) } }
CALL (*) {
    WHEN $edition_id IS NOT NULL THEN {
        MATCH (m:Edition {id: $edition_id})
        CREATE (r)-[:FOLLOWS]->(m)
    }
}
RETURN r.id AS recording_id
"""
SET_FOLLOWS_QUERY: LiteralString = """
MATCH (r:Recording {id: $recording_id})
OPTIONAL MATCH (r)-[existing:FOLLOWS]->()
DELETE existing
WITH DISTINCT r
MATCH (m:Edition {id: $edition_id})
CREATE (r)-[:FOLLOWS]->(m)
FINISH
"""
REMOVE_FOLLOWS_QUERY: LiteralString = """
MATCH (r:Recording {id: $recording_id})-[existing:FOLLOWS]->()
DELETE existing
FINISH
"""
GET_STORAGE_KEY_QUERY: LiteralString = """
MATCH (r:Recording {id: $recording_id})
RETURN r.audio_key AS key
"""
COUNT_CUED_FOLLOWERS_QUERY: LiteralString = """
MATCH (r:Recording)-[:FOLLOWS]->(:Edition {id: $edition_id})
WHERE EXISTS { (:Cue)-[:CUE_OF]->(r) }
RETURN count(r) AS count
"""
_DELETE_CUES_OF_MATCHED: LiteralString = """
CALL (r) {
    MATCH (t:Cue)-[:CUE_OF]->(r)
    OPTIONAL MATCH (span:Span)-[:SPAN_OF]->(t)
    DETACH DELETE span, t
}
"""
DELETE_QUERY: LiteralString = f"""
MATCH (r:Recording {{id: $recording_id}})
{_DELETE_CUES_OF_MATCHED}
{nomen.delete_names("r", "HAS_TITLE")}
DETACH DELETE r
FINISH
"""
GET_CUES_QUERY: LiteralString = """
MATCH (span:Span)-[:SPAN_OF]->(t:Cue)-[:CUE_OF]->(:Recording {id: $recording_id})
RETURN {start: span.start, end: span.end} AS span, t.start_ms AS start_ms, t.end_ms AS end_ms
ORDER BY t.start_ms
"""
CREATE_CUES_QUERY: LiteralString = """
MATCH (r:Recording {id: $recording_id})
UNWIND $cues AS cue
CREATE (:Span {start: cue.span.start, end: cue.span.end})
    -[:SPAN_OF]->(:Cue {start_ms: cue.start_ms, end_ms: cue.end_ms})
    -[:CUE_OF]->(r)
FINISH
"""
DELETE_CUES_QUERY: LiteralString = f"""
MATCH (r:Recording {{id: $recording_id}})
{_DELETE_CUES_OF_MATCHED}
FINISH
"""


@reading
async def get(tx: AsyncManagedTransaction, recording_id: str) -> RecordingOutput:
    return await fetch_one(
        tx,
        GET_BY_ID_QUERY,
        RecordingOutput,
        f"Recording with ID '{recording_id}' not found",
        field="recording",
        recording_id=recording_id,
    )


async def _validate_audio_exists(tx: AsyncManagedTransaction, audio_id: str) -> None:
    await require_node(tx, "Audio", audio_id, DataNotFoundError(f"Audio with ID '{audio_id}' not found"))


@reading
async def validate_target(tx: AsyncManagedTransaction, audio_id: str, edition_id: str | None) -> None:
    """Check the Audio exists and that the edition to follow is an edition of the text it recites."""
    await _validate_audio_exists(tx, audio_id)
    if edition_id is None:
        return
    await validate_edition_exists(tx, edition_id)
    result = await tx.run(FOLLOWS_TARGET_QUERY, audio_id=audio_id, edition_id=edition_id)
    record = await result.single(strict=True)
    if record["recited_text_id"] != record["edition_text_id"]:
        raise DataValidationError(
            f"Edition '{edition_id}' is an edition of text '{record['edition_text_id']}', "
            f"but audio '{audio_id}' recites text '{record['recited_text_id']}'"
        )


async def _get_links(tx: AsyncManagedTransaction, recording_id: str) -> tuple[str, str | None]:
    not_found = f"Recording with ID '{recording_id}' not found"
    record = await fetch_record(tx, LINKS_QUERY, not_found, recording_id=recording_id)
    return (record["audio_id"], record["edition_id"])


async def _lock(tx: AsyncManagedTransaction, recording_id: str, also: str | None = None) -> tuple[str, str | None]:
    """Lock the recording, then the edition it follows (and `also`); return its audio and that edition.

    Changing FOLLOWS or deleting the edition needs the recording's lock, so the edition read here stays put.
    """
    await lock_nodes(tx, "Recording", [recording_id])
    audio_id, followed = await _get_links(tx, recording_id)
    await lock_nodes(tx, "Edition", sorted({edition for edition in (followed, also) if edition}))
    return (audio_id, followed)


async def _lock_followed_edition(tx: AsyncManagedTransaction, recording_id: str) -> ContentState | None:
    """Lock the followed edition and the recording. Cue writes change that edition's annotations."""
    _, edition_id = await _lock(tx, recording_id)
    if edition_id is None:
        return None
    await advance_revision(tx, edition_id)
    return await read_state(tx, edition_id)


async def count_cued_followers(tx: AsyncManagedTransaction, edition_id: str) -> int:
    result = await tx.run(COUNT_CUED_FOLLOWERS_QUERY, edition_id=edition_id)
    return (await result.single(strict=True))["count"]


@writing
async def add(
    tx: AsyncManagedTransaction,
    audio_id: str,
    recording: RecordingInput,
    recording_id: str,
    *,
    audio_key: str,
    audio_format: AudioFormat,
    size_bytes: int,
) -> str:
    if recording.edition_id is not None:
        await lock_nodes(tx, "Edition", [recording.edition_id])
    await lock_nodes(tx, "Audio", [audio_id])
    await validate_target(tx, audio_id, recording.edition_id)
    title = recording.title.root if recording.title else None
    title_nomen_id = await nomen.create_optional(tx, title)
    return await create_id(
        tx,
        CREATE_QUERY,
        "recording_id",
        recording_id=recording_id,
        audio_id=audio_id,
        audio_key=audio_key,
        format=audio_format.value,
        size_bytes=size_bytes,
        duration_ms=recording.duration_ms,
        title_nomen_id=title_nomen_id,
        edition_id=recording.edition_id,
    )


@writing
async def delete(tx: AsyncManagedTransaction, recording_id: str) -> None:
    await _lock_followed_edition(tx, recording_id)
    await tx.run(DELETE_QUERY, recording_id=recording_id)


@reading
async def get_all_by_audio(tx: AsyncManagedTransaction, audio_id: str) -> list[RecordingOutput]:
    await _validate_audio_exists(tx, audio_id)
    return await fetch_all(tx, GET_BY_AUDIO_ID_QUERY, RecordingOutput, field="recording", audio_id=audio_id)


@reading
async def get_all_by_edition(tx: AsyncManagedTransaction, edition_id: str) -> list[RecordingOutput]:
    """The recordings that follow an edition, for follow-along playback."""
    await validate_edition_exists(tx, edition_id)
    return await fetch_all(tx, GET_BY_EDITION_ID_QUERY, RecordingOutput, field="recording", edition_id=edition_id)


async def get_storage_location(db: SessionSource, recording_id: str) -> str:
    """Return the immutable audio-object key stored with the recording."""
    not_found = f"Recording with ID '{recording_id}' not found"
    record = await query_record(db, GET_STORAGE_KEY_QUERY, not_found, recording_id=recording_id)
    return record["key"]


@reading
async def get_cues(tx: AsyncManagedTransaction, recording_id: str) -> list[RecordingCue]:
    _, edition_id = await _get_links(tx, recording_id)
    if edition_id is None:
        return []
    return await read_value(
        tx, edition_id, lambda tx: fetch_all(tx, GET_CUES_QUERY, RecordingCue, recording_id=recording_id)
    )


@writing
async def replace_cues(tx: AsyncManagedTransaction, recording_id: str, cues: list[RecordingCue]) -> None:
    state = await _lock_followed_edition(tx, recording_id)
    if state is None:
        raise DataValidationError(
            f"Recording '{recording_id}' does not follow an edition; set FOLLOWS before adding cues"
        )
    state.validate_span(max(cue.span.end for cue in cues))
    await tx.run(DELETE_CUES_QUERY, recording_id=recording_id)
    await tx.run(CREATE_CUES_QUERY, recording_id=recording_id, cues=[cue.model_dump() for cue in cues])


@writing
async def delete_cues(tx: AsyncManagedTransaction, recording_id: str) -> None:
    if await _lock_followed_edition(tx, recording_id) is not None:
        await tx.run(DELETE_CUES_QUERY, recording_id=recording_id)


@writing
async def set_follows(tx: AsyncManagedTransaction, recording_id: str, edition_id: str) -> None:
    """Follow an edition. Cues point into the old edition, so a change of edition deletes them."""
    audio_id, current = await _lock(tx, recording_id, also=edition_id)
    await validate_target(tx, audio_id, edition_id)
    if current == edition_id:
        return
    if current is not None:
        await advance_revision(tx, current)
    await tx.run(DELETE_CUES_QUERY, recording_id=recording_id)
    await tx.run(SET_FOLLOWS_QUERY, recording_id=recording_id, edition_id=edition_id)


@writing
async def remove_follows(tx: AsyncManagedTransaction, recording_id: str) -> None:
    """Stop following an edition and delete the cues that pointed into it."""
    if await _lock_followed_edition(tx, recording_id) is None:
        return
    await tx.run(DELETE_CUES_QUERY, recording_id=recording_id)
    await tx.run(REMOVE_FOLLOWS_QUERY, recording_id=recording_id)


@writing
async def update(tx: AsyncManagedTransaction, recording_id: str, patch: RecordingPatch) -> RecordingOutput:
    await lock_nodes(tx, "Recording", [recording_id])
    not_found = DataNotFoundError(f"Recording with ID '{recording_id}' not found")
    await require_node(tx, "Recording", recording_id, not_found)
    await set_properties(tx, "Recording", recording_id, patch.model_dump(include={"duration_ms"}))
    if patch.title is not MISSING:
        await nomen.replace(tx, "Recording", recording_id, patch.title.root)
    return await get(tx, recording_id)
