from typing import TYPE_CHECKING, LiteralString

from pydantic import MISSING

from exceptions import DataNotFoundError
from identifier import generate_id
from models.audio import AudioInput, AudioOutput, AudioPatch

from .contribution_database import ContributionDatabase
from .cypher import replace_language, replace_license
from .database_validator import DatabaseValidator
from .locking import lock_nodes
from .nomen_database import NomenDatabase
from .repository import (
    SessionRepository,
    fetch_all,
    fetch_one,
    fetch_record,
    raise_if_blockers,
    require_node,
    set_properties,
)
from .text_database import TextDatabase

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction


AUDIO_LABEL: LiteralString = "Audio"


class AudioDatabase(SessionRepository):
    """One performance of a text: an Expression beside the Text, under the same Work."""

    _AUDIO_RETURN: LiteralString = f"""
    RETURN {{
        id: a.id, date: a.date,
        text_id: t.id,
        license: coalesce([(a)-[:HAS_LICENSE]->(license:LicenseType) | license.name][0], "public"),
        language: [(a)-[lang_rel:HAS_LANGUAGE]->(lang:Language) | coalesce(lang_rel.bcp47, lang.code)][0],
        title: {NomenDatabase.optional_localized_map("(a)-[:HAS_TITLE]->(:Nomen)")},
        contributions: {ContributionDatabase.return_list("a")},
        recordings: apoc.coll.sort([(r:Recording)-[:RENDITION_OF]->(a) | r.id])
    }} AS audio
    """

    GET_BY_ID_QUERY: LiteralString = f"""
    MATCH (a:Audio {{id: $audio_id}})-[:RECITATION_OF]->(t:Text)
    {_AUDIO_RETURN}
    """

    GET_BY_TEXT_ID_QUERY: LiteralString = f"""
    MATCH (a:Audio)-[:RECITATION_OF]->(t:Text {{id: $text_id}})
    WITH a, t
    ORDER BY a.id
    {_AUDIO_RETURN}
    """

    # The Work comes from the recited Text, as a translation's does, so the two cannot disagree.
    CREATE_QUERY: LiteralString = """
    MATCH (t:Text {id: $text_id})-[:TEXT_OF]->(w:Work)
    MATCH (t)-[text_language_rel:HAS_LANGUAGE]->(text_language:Language)
    MATCH (license:LicenseType {name: $license})
    OPTIONAL MATCH (title:Nomen {id: $title_nomen_id})
    CREATE (a:Audio {id: $audio_id, date: $date})
    CREATE (a)-[:AUDIO_OF]->(w), (a)-[:RECITATION_OF]->(t), (a)-[:HAS_LICENSE]->(license)
    CALL (*) { WHEN title IS NOT NULL THEN { CREATE (a)-[:HAS_TITLE]->(title) } }
    CALL (*) {
        WHEN $language_code IS NULL THEN {
            CREATE (a)-[:HAS_LANGUAGE {bcp47: coalesce(text_language_rel.bcp47, text_language.code)}]->(text_language)
        }
        ELSE {
            MATCH (lang:Language {code: $language_code})
            CREATE (a)-[:HAS_LANGUAGE {bcp47: $language}]->(lang)
        }
    }
    RETURN a.id AS audio_id
    """

    UPDATE_LICENSE_QUERY: LiteralString = replace_license("Audio")

    UPDATE_LANGUAGE_QUERY: LiteralString = replace_language("Audio")

    DELETE_CHECK_QUERY: LiteralString = """
    MATCH (a:Audio {id: $audio_id})
    RETURN count { (:Recording)-[:RENDITION_OF]->(a) } AS recording_count
    """

    DELETE_QUERY: LiteralString = """
    MATCH (a:Audio {id: $audio_id})
    OPTIONAL MATCH (a)-[:HAS_CONTRIBUTION]->(c:Contribution)
    OPTIONAL MATCH (a)-[:HAS_TITLE]->(n:Nomen)-[:HAS_LOCALIZATION]->(lt:LocalizedText)
    DETACH DELETE a, c, n, lt
    FINISH
    """

    async def get(self, audio_id: str) -> AudioOutput:
        return await self.execute_read(self.get_with_transaction, audio_id)

    @staticmethod
    async def get_with_transaction(tx: AsyncManagedTransaction, audio_id: str) -> AudioOutput:
        return await fetch_one(
            tx,
            AudioDatabase.GET_BY_ID_QUERY,
            AudioOutput,
            f"Audio with ID '{audio_id}' not found",
            field="audio",
            audio_id=audio_id,
        )

    async def get_all(self, text_id: str) -> list[AudioOutput]:
        async def read(tx: AsyncManagedTransaction) -> list[AudioOutput]:
            await require_node(tx, "Text", text_id, DataNotFoundError(f"Text with ID '{text_id}' not found"))
            return await fetch_all(tx, AudioDatabase.GET_BY_TEXT_ID_QUERY, AudioOutput, field="audio", text_id=text_id)

        return await self.execute_read(read)

    async def create(self, text_id: str, audio: AudioInput) -> str:
        # Generated outside the transaction function, so a retried transaction reuses the same ID.
        audio_id = generate_id()
        return await self.execute_write(AudioDatabase.create_with_transaction, text_id, audio, audio_id)

    @staticmethod
    async def create_with_transaction(
        tx: AsyncManagedTransaction, text_id: str, audio: AudioInput, audio_id: str
    ) -> str:
        await TextDatabase.lock_with_work(tx, text_id)
        await require_node(tx, "Text", text_id, DataNotFoundError(f"Text with ID '{text_id}' not found"))
        await DatabaseValidator.validate_contribution_references(tx, audio.contributions)

        title = audio.title.root if audio.title else None
        title_nomen_id = await NomenDatabase.create_optional_with_transaction(tx, title)
        language_code = await DatabaseValidator.validated_language_code(tx, audio.language)
        result = await tx.run(
            AudioDatabase.CREATE_QUERY,
            audio_id=audio_id,
            text_id=text_id,
            date=audio.date,
            license=audio.license.value,
            title_nomen_id=title_nomen_id,
            language=audio.language,
            language_code=language_code,
        )

        record = await result.single(strict=True)
        created_audio_id = str(record["audio_id"])
        await ContributionDatabase.create_all_with_transaction(tx, AUDIO_LABEL, created_audio_id, audio.contributions)

        return created_audio_id

    async def update(self, audio_id: str, patch: AudioPatch) -> AudioOutput:
        async def write(tx: AsyncManagedTransaction) -> AudioOutput:
            await lock_nodes(tx, "Audio", [audio_id])
            await require_node(tx, "Audio", audio_id, DataNotFoundError(f"Audio with ID '{audio_id}' not found"))
            await set_properties(tx, AUDIO_LABEL, audio_id, patch.model_dump(include={"date"}))

            if patch.license is not MISSING:
                await tx.run(AudioDatabase.UPDATE_LICENSE_QUERY, id=audio_id, license=patch.license.value)

            if patch.language is not MISSING:
                language_code = await DatabaseValidator.validated_language_code(tx, patch.language)
                await tx.run(
                    AudioDatabase.UPDATE_LANGUAGE_QUERY, id=audio_id, bcp47=patch.language, language_code=language_code
                )

            if patch.title is not MISSING:
                await NomenDatabase.replace_with_transaction(tx, AUDIO_LABEL, audio_id, patch.title.root)

            if patch.contributions is not MISSING:
                await ContributionDatabase.replace_with_transaction(tx, AUDIO_LABEL, audio_id, patch.contributions)
            return await self.get_with_transaction(tx, audio_id)

        return await self.execute_write(write)

    async def delete(self, audio_id: str) -> None:
        async def write(tx: AsyncManagedTransaction) -> None:
            await lock_nodes(tx, "Audio", [audio_id])
            not_found = f"Audio with ID '{audio_id}' not found"
            record = await fetch_record(tx, AudioDatabase.DELETE_CHECK_QUERY, not_found, audio_id=audio_id)
            raise_if_blockers(
                f"Audio '{audio_id}' cannot be deleted because it has", {"recording(s)": record["recording_count"]}
            )
            await tx.run(AudioDatabase.DELETE_QUERY, audio_id=audio_id)

        await self.execute_write(write)
