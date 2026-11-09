from typing import TYPE_CHECKING, LiteralString

from exceptions import DataConflictError, DataNotFoundError, DataValidationError
from models.enums import AudioFormat
from models.recording import RecordingCue, RecordingInput, RecordingOutput, RecordingPatch

from .content_state import ContentState, advance_revision, read_state, read_value
from .database_validator import DatabaseValidator
from .locking import lock_nodes
from .nomen_database import NomenDatabase

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction, AsyncSession

    from .database import Database


class RecordingDatabase:
    """One audio file of an Audio. Cues align it with the edition it follows."""

    _RECORDING_RETURN: LiteralString = """
    RETURN {
        id: r.id, format: r.format, size_bytes: r.size_bytes, duration_ms: r.duration_ms,
        audio_id: a.id,
        text_id: [(a)-[:RECITATION_OF]->(t:Text) | t.id][0],
        edition_id: [(r)-[:FOLLOWS]->(m:Edition) | m.id][0],
        title: CASE WHEN EXISTS {
            (r)-[:HAS_TITLE]->(:Nomen)-[:HAS_LOCALIZATION]->(:LocalizedText)
        } THEN apoc.map.fromPairs([(r)-[:HAS_TITLE]->(n:Nomen)-[:HAS_LOCALIZATION]->
            (lt:LocalizedText)-[title_rel:HAS_LANGUAGE]->(title_lang:Language) |
            [coalesce(title_rel.bcp47, title_lang.code), lt.text]]) ELSE null END
    } AS recording
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

    AUDIO_EXISTS_QUERY: LiteralString = """
    MATCH (a:Audio {id: $audio_id}) RETURN a.id AS id
    """

    # Rule: a recording may only follow an edition of the text its Audio recites.
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

    UPDATE_PROPERTIES_QUERY: LiteralString = """
    MATCH (r:Recording {id: $recording_id})
    SET r += $properties
    FINISH
    """

    DELETE_TITLE_QUERY: LiteralString = """
    MATCH (r:Recording {id: $recording_id})-[:HAS_TITLE]->(n:Nomen)
    OPTIONAL MATCH (n)-[:HAS_LOCALIZATION]->(lt:LocalizedText)
    DETACH DELETE n, lt
    FINISH
    """

    LINK_TITLE_QUERY: LiteralString = """
    MATCH (r:Recording {id: $recording_id})
    MATCH (n:Nomen {id: $nomen_id})
    CREATE (r)-[:HAS_TITLE]->(n)
    FINISH
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
    OPTIONAL MATCH (r)-[:HAS_TITLE]->(n:Nomen)-[:HAS_LOCALIZATION]->(lt:LocalizedText)
    DETACH DELETE r, n, lt
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

    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def session(self) -> AsyncSession:
        return self._db.get_session()

    async def get(self, recording_id: str) -> RecordingOutput:
        async with self.session as session:
            return await session.execute_read(self.get_with_transaction, recording_id)

    @staticmethod
    async def get_with_transaction(tx: AsyncManagedTransaction, recording_id: str) -> RecordingOutput:
        result = await tx.run(RecordingDatabase.GET_BY_ID_QUERY, recording_id=recording_id)
        record = await result.single()
        if record is None:
            raise DataNotFoundError(f"Recording with ID '{recording_id}' not found")
        return RecordingOutput.model_validate(record["recording"])

    async def get_all_by_audio(self, audio_id: str) -> list[RecordingOutput]:
        async def read(tx: AsyncManagedTransaction) -> list[RecordingOutput]:
            await RecordingDatabase._validate_audio_exists(tx, audio_id)
            result = await tx.run(RecordingDatabase.GET_BY_AUDIO_ID_QUERY, audio_id=audio_id)
            return [RecordingOutput.model_validate(record["recording"]) for record in await result.data()]

        async with self.session as session:
            return await session.execute_read(read)

    async def get_all_by_edition(self, edition_id: str) -> list[RecordingOutput]:
        """The recordings that follow an edition, for follow-along playback."""

        async def read(tx: AsyncManagedTransaction) -> list[RecordingOutput]:
            await DatabaseValidator.validate_edition_exists(tx, edition_id)
            result = await tx.run(RecordingDatabase.GET_BY_EDITION_ID_QUERY, edition_id=edition_id)
            return [RecordingOutput.model_validate(record["recording"]) for record in await result.data()]

        async with self.session as session:
            return await session.execute_read(read)

    async def get_storage_location(self, recording_id: str) -> str:
        """Return the immutable audio-object key stored with the recording."""

        async def read(tx: AsyncManagedTransaction) -> str:
            result = await tx.run(RecordingDatabase.GET_STORAGE_KEY_QUERY, recording_id=recording_id)
            record = await result.single()
            if record is None:
                raise DataNotFoundError(f"Recording with ID '{recording_id}' not found")
            if record["key"] is None:
                raise DataValidationError(f"Recording '{recording_id}' requires the recording migration")
            return record["key"]

        async with self.session as session:
            return await session.execute_read(read)

    @staticmethod
    async def _validate_audio_exists(tx: AsyncManagedTransaction, audio_id: str) -> None:
        result = await tx.run(RecordingDatabase.AUDIO_EXISTS_QUERY, audio_id=audio_id)
        if await result.single() is None:
            raise DataNotFoundError(f"Audio with ID '{audio_id}' not found")

    @staticmethod
    async def validate_target_with_transaction(
        tx: AsyncManagedTransaction, audio_id: str, edition_id: str | None
    ) -> None:
        """Check the Audio exists and that the edition to follow is an edition of the text it recites."""
        await RecordingDatabase._validate_audio_exists(tx, audio_id)
        if edition_id is None:
            return
        await DatabaseValidator.validate_edition_exists(tx, edition_id)
        result = await tx.run(RecordingDatabase.FOLLOWS_TARGET_QUERY, audio_id=audio_id, edition_id=edition_id)
        record = await result.single(strict=True)
        if record["recited_text_id"] != record["edition_text_id"]:
            raise DataValidationError(
                f"Edition '{edition_id}' is an edition of text '{record['edition_text_id']}', "
                f"but audio '{audio_id}' recites text '{record['recited_text_id']}'"
            )

    async def validate_target(self, audio_id: str, edition_id: str | None) -> None:
        async with self.session as session:
            await session.execute_read(RecordingDatabase.validate_target_with_transaction, audio_id, edition_id)

    @staticmethod
    async def _get_links(tx: AsyncManagedTransaction, recording_id: str) -> tuple[str, str | None]:
        result = await tx.run(RecordingDatabase.LINKS_QUERY, recording_id=recording_id)
        record = await result.single()
        if record is None:
            raise DataNotFoundError(f"Recording with ID '{recording_id}' not found")
        return record["audio_id"], record["edition_id"]

    @staticmethod
    async def _lock(tx: AsyncManagedTransaction, recording_id: str, also: str | None = None) -> tuple[str, str | None]:
        """Lock the followed edition (and `also`) before the recording, the order content edits use.

        FOLLOWS can change, or its edition be deleted, while this waits for a lock. Re-read after locking and
        lock again until the edition followed is one already locked; return the audio and that edition.
        """
        for _ in range(3):
            _, followed = await RecordingDatabase._get_links(tx, recording_id)
            await lock_nodes(tx, "Edition", sorted({edition for edition in (followed, also) if edition}))
            await lock_nodes(tx, "Recording", [recording_id])
            audio_id, current = await RecordingDatabase._get_links(tx, recording_id)
            if current == followed:
                return audio_id, current
        raise DataConflictError(f"Recording '{recording_id}' keeps changing its edition; retry the operation")

    @staticmethod
    async def _lock_followed_edition(tx: AsyncManagedTransaction, recording_id: str) -> ContentState | None:
        """Lock the followed edition and the recording. Cue writes change that edition's annotations."""
        _, edition_id = await RecordingDatabase._lock(tx, recording_id)
        if edition_id is None:
            return None
        await advance_revision(tx, edition_id)
        return await read_state(tx, edition_id)

    async def get_cues(self, recording_id: str) -> list[RecordingCue]:
        async def cues(tx: AsyncManagedTransaction) -> list[RecordingCue]:
            result = await tx.run(RecordingDatabase.GET_CUES_QUERY, recording_id=recording_id)
            return [RecordingCue.model_validate(record) for record in await result.data()]

        async def read(tx: AsyncManagedTransaction) -> list[RecordingCue]:
            _, edition_id = await RecordingDatabase._get_links(tx, recording_id)
            if edition_id is None:
                return []
            try:
                return await read_value(tx, edition_id, cues)
            except DataNotFoundError:
                # The edition was deleted after the links were read; the recording then follows nothing.
                if (await RecordingDatabase._get_links(tx, recording_id))[1] is None:
                    return []
                raise

        async with self.session as session:
            return await session.execute_read(read)

    async def replace_cues(self, recording_id: str, cues: list[RecordingCue]) -> None:
        async def write(tx: AsyncManagedTransaction) -> None:
            state = await RecordingDatabase._lock_followed_edition(tx, recording_id)
            if state is None:
                raise DataValidationError(
                    f"Recording '{recording_id}' does not follow an edition; set FOLLOWS before adding cues"
                )
            state.validate_span(max(cue.span.end for cue in cues))
            await tx.run(RecordingDatabase.DELETE_CUES_QUERY, recording_id=recording_id)
            await tx.run(
                RecordingDatabase.CREATE_CUES_QUERY,
                recording_id=recording_id,
                cues=[cue.model_dump() for cue in cues],
            )

        async with self.session as session:
            await session.execute_write(write)

    async def delete_cues(self, recording_id: str) -> None:
        async def write(tx: AsyncManagedTransaction) -> None:
            if await RecordingDatabase._lock_followed_edition(tx, recording_id) is not None:
                await tx.run(RecordingDatabase.DELETE_CUES_QUERY, recording_id=recording_id)

        async with self.session as session:
            await session.execute_write(write)

    async def set_follows(self, recording_id: str, edition_id: str) -> None:
        """Follow an edition. Cues point into the old edition, so a change of edition deletes them."""

        async def write(tx: AsyncManagedTransaction) -> None:
            audio_id, current = await RecordingDatabase._lock(tx, recording_id, also=edition_id)
            # Validate under the locks, so the edition cannot be deleted between the check and the write.
            await RecordingDatabase.validate_target_with_transaction(tx, audio_id, edition_id)
            if current == edition_id:
                return
            if current is not None:
                await advance_revision(tx, current)
            await tx.run(RecordingDatabase.DELETE_CUES_QUERY, recording_id=recording_id)
            await tx.run(RecordingDatabase.SET_FOLLOWS_QUERY, recording_id=recording_id, edition_id=edition_id)

        async with self.session as session:
            await session.execute_write(write)

    async def remove_follows(self, recording_id: str) -> None:
        """Stop following an edition and delete the cues that pointed into it."""

        async def write(tx: AsyncManagedTransaction) -> None:
            if await RecordingDatabase._lock_followed_edition(tx, recording_id) is None:
                return
            await tx.run(RecordingDatabase.DELETE_CUES_QUERY, recording_id=recording_id)
            await tx.run(RecordingDatabase.REMOVE_FOLLOWS_QUERY, recording_id=recording_id)

        async with self.session as session:
            await session.execute_write(write)

    @staticmethod
    async def count_cued_followers_with_transaction(tx: AsyncManagedTransaction, edition_id: str) -> int:
        result = await tx.run(RecordingDatabase.COUNT_CUED_FOLLOWERS_QUERY, edition_id=edition_id)
        return (await result.single(strict=True))["count"]

    async def add(
        self,
        audio_id: str,
        recording: RecordingInput,
        recording_id: str,
        audio_key: str,
        audio_format: AudioFormat,
        size_bytes: int,
    ) -> str:
        async with self.session as session:
            return await session.execute_write(
                RecordingDatabase.add_with_transaction,
                audio_id,
                recording,
                recording_id,
                audio_key,
                audio_format,
                size_bytes,
            )

    @staticmethod
    async def add_with_transaction(
        tx: AsyncManagedTransaction,
        audio_id: str,
        recording: RecordingInput,
        recording_id: str,
        audio_key: str,
        audio_format: AudioFormat,
        size_bytes: int,
    ) -> str:
        # Editions first, as everywhere else, so an edition delete cannot interleave with the FOLLOWS write.
        if recording.edition_id is not None:
            await lock_nodes(tx, "Edition", [recording.edition_id])
        await lock_nodes(tx, "Audio", [audio_id])
        await RecordingDatabase.validate_target_with_transaction(tx, audio_id, recording.edition_id)

        title_nomen_id = None
        if recording.title:
            title_nomen_id = await NomenDatabase.create_with_transaction(tx, recording.title.root)

        result = await tx.run(
            RecordingDatabase.CREATE_QUERY,
            recording_id=recording_id,
            audio_id=audio_id,
            audio_key=audio_key,
            format=audio_format.value,
            size_bytes=size_bytes,
            duration_ms=recording.duration_ms,
            title_nomen_id=title_nomen_id,
            edition_id=recording.edition_id,
        )

        record = await result.single(strict=True)
        return str(record["recording_id"])

    async def update(self, recording_id: str, patch: RecordingPatch) -> RecordingOutput:
        async def write(tx: AsyncManagedTransaction) -> RecordingOutput:
            await lock_nodes(tx, "Recording", [recording_id])
            result = await tx.run("MATCH (r:Recording {id: $id}) RETURN r.id AS id", id=recording_id)
            if await result.single() is None:
                raise DataNotFoundError(f"Recording '{recording_id}' not found")
            properties = patch.model_dump(include={"duration_ms"}, exclude_unset=True)
            if properties:
                await tx.run(
                    RecordingDatabase.UPDATE_PROPERTIES_QUERY,
                    recording_id=recording_id,
                    properties=properties,
                )

            if patch.title is not None:
                await tx.run(RecordingDatabase.DELETE_TITLE_QUERY, recording_id=recording_id)
                nomen_id = await NomenDatabase.create_with_transaction(tx, patch.title.root)
                await tx.run(RecordingDatabase.LINK_TITLE_QUERY, recording_id=recording_id, nomen_id=nomen_id)
            return await self.get_with_transaction(tx, recording_id)

        async with self.session as session:
            return await session.execute_write(write)

    async def delete(self, recording_id: str) -> None:
        async with self.session as session:
            await session.execute_write(RecordingDatabase.delete_with_transaction, recording_id)

    @staticmethod
    async def delete_with_transaction(tx: AsyncManagedTransaction, recording_id: str) -> None:
        # Its cues are annotations of the followed edition, so lock and touch that edition as a cue delete does.
        await RecordingDatabase._lock_followed_edition(tx, recording_id)
        await tx.run(RecordingDatabase.DELETE_QUERY, recording_id=recording_id)
