from typing import TYPE_CHECKING, LiteralString

from exceptions import DataConflictError, DataNotFoundError
from identifier import generate_id
from models.audio import AudioInput, AudioOutput, AudioPatch

from .contribution_database import ContributionDatabase, contributions_return
from .database_validator import DatabaseValidator
from .locking import lock_nodes
from .nomen_database import NomenDatabase
from .text_database import TextDatabase

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction, AsyncSession

    from .database import Database

AUDIO_LABEL: LiteralString = "Audio"


class AudioDatabase:
    """One performance of a text: an Expression beside the Text, under the same Work."""

    _AUDIO_RETURN: LiteralString = f"""
    RETURN {{
        id: a.id, date: a.date,
        text_id: t.id,
        license: coalesce([(a)-[:HAS_LICENSE]->(license:LicenseType) | license.name][0], "public"),
        language: [(a)-[lang_rel:HAS_LANGUAGE]->(lang:Language) | coalesce(lang_rel.bcp47, lang.code)][0],
        title: CASE WHEN EXISTS {{
            (a)-[:HAS_TITLE]->(:Nomen)-[:HAS_LOCALIZATION]->(:LocalizedText)
        }} THEN apoc.map.fromPairs([(a)-[:HAS_TITLE]->(n:Nomen)-[:HAS_LOCALIZATION]->
            (lt:LocalizedText)-[title_rel:HAS_LANGUAGE]->(title_lang:Language) |
            [coalesce(title_rel.bcp47, title_lang.code), lt.text]]) ELSE null END,
        contributions: {contributions_return("a")},
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

    TEXT_EXISTS_QUERY: LiteralString = """
    MATCH (t:Text {id: $text_id}) RETURN t.id AS id
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

    UPDATE_PROPERTIES_QUERY: LiteralString = """
    MATCH (a:Audio {id: $audio_id})
    SET a += $properties
    FINISH
    """

    UPDATE_LICENSE_QUERY: LiteralString = """
    MATCH (a:Audio {id: $audio_id})
    OPTIONAL MATCH (a)-[existing:HAS_LICENSE]->()
    DELETE existing
    WITH a
    MATCH (license:LicenseType {name: $license})
    MERGE (a)-[:HAS_LICENSE]->(license)
    FINISH
    """

    UPDATE_LANGUAGE_QUERY: LiteralString = """
    MATCH (a:Audio {id: $audio_id})
    OPTIONAL MATCH (a)-[existing:HAS_LANGUAGE]->()
    DELETE existing
    WITH a
    MATCH (lang:Language {code: $language_code})
    MERGE (a)-[:HAS_LANGUAGE {bcp47: $language}]->(lang)
    FINISH
    """

    DELETE_TITLE_QUERY: LiteralString = """
    MATCH (a:Audio {id: $audio_id})-[:HAS_TITLE]->(n:Nomen)
    OPTIONAL MATCH (n)-[:HAS_LOCALIZATION]->(lt:LocalizedText)
    DETACH DELETE n, lt
    FINISH
    """

    LINK_TITLE_QUERY: LiteralString = """
    MATCH (a:Audio {id: $audio_id})
    MATCH (n:Nomen {id: $nomen_id})
    CREATE (a)-[:HAS_TITLE]->(n)
    FINISH
    """

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

    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def session(self) -> AsyncSession:
        return self._db.get_session()

    async def get(self, audio_id: str) -> AudioOutput:
        async with self.session as session:
            return await session.execute_read(self.get_with_transaction, audio_id)

    @staticmethod
    async def get_with_transaction(tx: AsyncManagedTransaction, audio_id: str) -> AudioOutput:
        result = await tx.run(AudioDatabase.GET_BY_ID_QUERY, audio_id=audio_id)
        record = await result.single()
        if record is None:
            raise DataNotFoundError(f"Audio with ID '{audio_id}' not found")
        return AudioOutput.model_validate(record["audio"])

    async def get_all(self, text_id: str) -> list[AudioOutput]:
        async def read(tx: AsyncManagedTransaction) -> list[AudioOutput]:
            await AudioDatabase._validate_text_exists(tx, text_id)
            result = await tx.run(AudioDatabase.GET_BY_TEXT_ID_QUERY, text_id=text_id)
            return [AudioOutput.model_validate(record["audio"]) for record in await result.data()]

        async with self.session as session:
            return await session.execute_read(read)

    @staticmethod
    async def _validate_text_exists(tx: AsyncManagedTransaction, text_id: str) -> None:
        result = await tx.run(AudioDatabase.TEXT_EXISTS_QUERY, text_id=text_id)
        if await result.single() is None:
            raise DataNotFoundError(f"Text with ID '{text_id}' not found")

    async def create(self, text_id: str, audio: AudioInput) -> str:
        # Generated outside the transaction function, so a retried transaction reuses the same ID.
        audio_id = generate_id()
        async with self.session as session:
            return await session.execute_write(AudioDatabase.create_with_transaction, text_id, audio, audio_id)

    @staticmethod
    async def create_with_transaction(
        tx: AsyncManagedTransaction, text_id: str, audio: AudioInput, audio_id: str
    ) -> str:
        await TextDatabase.lock_with_work(tx, text_id)
        await AudioDatabase._validate_text_exists(tx, text_id)
        await DatabaseValidator.validate_contribution_references(tx, audio.contributions)

        title_nomen_id = None
        if audio.title:
            title_nomen_id = await NomenDatabase.create_with_transaction(tx, audio.title.root)

        language_code = None
        if audio.language:
            language_code = audio.language.split("-")[0].lower()
            await DatabaseValidator.validate_language_code_exists(tx, language_code)

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
        for contribution in audio.contributions:
            await ContributionDatabase.create_with_transaction(tx, AUDIO_LABEL, created_audio_id, contribution)

        return created_audio_id

    async def update(self, audio_id: str, patch: AudioPatch) -> AudioOutput:
        async def write(tx: AsyncManagedTransaction) -> AudioOutput:
            await lock_nodes(tx, "Audio", [audio_id])
            result = await tx.run("MATCH (a:Audio {id: $id}) RETURN a.id AS id", id=audio_id)
            if await result.single() is None:
                raise DataNotFoundError(f"Audio '{audio_id}' not found")
            properties = patch.model_dump(include={"date"}, exclude_unset=True)
            if properties:
                await tx.run(AudioDatabase.UPDATE_PROPERTIES_QUERY, audio_id=audio_id, properties=properties)

            if patch.license is not None:
                await tx.run(AudioDatabase.UPDATE_LICENSE_QUERY, audio_id=audio_id, license=patch.license.value)

            if patch.language is not None:
                language_code = patch.language.split("-")[0].lower()
                await DatabaseValidator.validate_language_code_exists(tx, language_code)
                await tx.run(
                    AudioDatabase.UPDATE_LANGUAGE_QUERY,
                    audio_id=audio_id,
                    language=patch.language,
                    language_code=language_code,
                )

            if patch.title is not None:
                await tx.run(AudioDatabase.DELETE_TITLE_QUERY, audio_id=audio_id)
                nomen_id = await NomenDatabase.create_with_transaction(tx, patch.title.root)
                await tx.run(AudioDatabase.LINK_TITLE_QUERY, audio_id=audio_id, nomen_id=nomen_id)

            if patch.contributions is not None:
                await DatabaseValidator.validate_contribution_references(tx, patch.contributions)
                await ContributionDatabase.delete_all_with_transaction(tx, AUDIO_LABEL, audio_id)
                for contribution in patch.contributions:
                    await ContributionDatabase.create_with_transaction(tx, AUDIO_LABEL, audio_id, contribution)
            return await self.get_with_transaction(tx, audio_id)

        async with self.session as session:
            return await session.execute_write(write)

    async def delete(self, audio_id: str) -> None:
        async def write(tx: AsyncManagedTransaction) -> None:
            await lock_nodes(tx, "Audio", [audio_id])
            result = await tx.run(AudioDatabase.DELETE_CHECK_QUERY, audio_id=audio_id)
            record = await result.single()
            if record is None:
                raise DataNotFoundError(f"Audio '{audio_id}' not found")
            if record["recording_count"]:
                raise DataConflictError(
                    f"Audio '{audio_id}' cannot be deleted because it has {record['recording_count']} recording(s)"
                )
            await tx.run(AudioDatabase.DELETE_QUERY, audio_id=audio_id)

        async with self.session as session:
            await session.execute_write(write)
