from typing import TYPE_CHECKING, LiteralString

from pydantic import MISSING

from errors import DataNotFoundError
from identifier import generate_id
from models.audio import AudioInput, AudioOutput, AudioPatch

from . import contribution, language, nomen
from .cypher import replace_language, replace_license
from .locking import lock_nodes
from .repository import (
    create_id,
    fetch_all,
    fetch_one,
    fetch_record,
    raise_if_blockers,
    reading,
    require_node,
    set_properties,
    writing,
)

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction

AUDIO_LABEL: LiteralString = "Audio"
_AUDIO_RETURN: LiteralString = f"""
RETURN {{
    id: a.id, date: a.date,
    text_id: t.id,
    license: coalesce([(a)-[:HAS_LICENSE]->(license:LicenseType) | license.name][0], "public"),
    language: [(a)-[lang_rel:HAS_LANGUAGE]->(lang:Language) | coalesce(lang_rel.bcp47, lang.code)][0],
    title: {nomen.optional_localized_map("(a)-[:HAS_TITLE]->(:Nomen)")},
    contributions: {contribution.return_list("a")},
    recordings: apoc.coll.sort([(r:Recording)-[:RENDITION_OF]->(a) | r.id])
}} AS audio
"""
GET_BY_ID_QUERY: LiteralString = f"""
MATCH (a:Audio {{id: $audio_id}})-[:RECITATION_OF]->(t:Text)
{_AUDIO_RETURN}
"""
GET_BY_TEXT_ID_QUERY: LiteralString = f"""
MATCH (a:Audio)-[:RECITATION_OF]->(t:Text {{id: $text_id}})
{_AUDIO_RETURN}
ORDER BY a.id
"""
CREATE_QUERY: LiteralString = """
MATCH (t:Text {id: $text_id})-[text_language_rel:HAS_LANGUAGE]->(text_language:Language)
MATCH (lang:Language {code: coalesce($language_code, text_language.code)})
MATCH (license:LicenseType {name: $license})
OPTIONAL MATCH (title:Nomen {id: $title_nomen_id})
CREATE (a:Audio {id: $audio_id, date: $date})
CREATE (a)-[:RECITATION_OF]->(t), (a)-[:HAS_LICENSE]->(license),
       (a)-[:HAS_LANGUAGE {bcp47: coalesce($language, text_language_rel.bcp47, text_language.code)}]->(lang)
CALL (*) { WHEN title IS NOT NULL THEN { CREATE (a)-[:HAS_TITLE]->(title) } }
RETURN a.id AS audio_id
"""
UPDATE_LICENSE_QUERY: LiteralString = replace_license("Audio")
UPDATE_LANGUAGE_QUERY: LiteralString = replace_language("Audio")
DELETE_CHECK_QUERY: LiteralString = """
MATCH (a:Audio {id: $audio_id})
RETURN count { (:Recording)-[:RENDITION_OF]->(a) } AS recording_count
"""
DELETE_QUERY: LiteralString = f"""
MATCH (a:Audio {{id: $audio_id}})
{nomen.delete_names("a", "HAS_TITLE")}
OPTIONAL MATCH (a)-[:HAS_CONTRIBUTION]->(c:Contribution)
DETACH DELETE a, c
FINISH
"""


@reading
async def get(tx: AsyncManagedTransaction, audio_id: str) -> AudioOutput:
    return await fetch_one(
        tx, GET_BY_ID_QUERY, AudioOutput, f"Audio with ID '{audio_id}' not found", field="audio", audio_id=audio_id
    )


@writing
async def create(tx: AsyncManagedTransaction, text_id: str, audio: AudioInput, audio_id: str | None = None) -> str:
    audio_id = audio_id or generate_id()
    await lock_nodes(tx, "Text", [text_id])
    await require_node(tx, "Text", text_id, DataNotFoundError(f"Text with ID '{text_id}' not found"))
    await contribution.validate_contribution_references(tx, audio.contributions)
    title_nomen_id = await nomen.create_optional(tx, audio.title.root if audio.title else None)
    language_code = await language.validated_language_code(tx, audio.language)
    created_audio_id = await create_id(
        tx,
        CREATE_QUERY,
        "audio_id",
        audio_id=audio_id,
        text_id=text_id,
        date=audio.date,
        license=audio.license.value,
        title_nomen_id=title_nomen_id,
        language=audio.language,
        language_code=language_code,
    )
    await contribution.create_all(tx, AUDIO_LABEL, created_audio_id, audio.contributions)
    return created_audio_id


@reading
async def get_all(tx: AsyncManagedTransaction, text_id: str) -> list[AudioOutput]:
    await require_node(tx, "Text", text_id, DataNotFoundError(f"Text with ID '{text_id}' not found"))
    return await fetch_all(tx, GET_BY_TEXT_ID_QUERY, AudioOutput, field="audio", text_id=text_id)


@writing
async def update(tx: AsyncManagedTransaction, audio_id: str, patch: AudioPatch) -> AudioOutput:
    await lock_nodes(tx, "Audio", [audio_id])
    await require_node(tx, "Audio", audio_id, DataNotFoundError(f"Audio with ID '{audio_id}' not found"))
    await set_properties(tx, AUDIO_LABEL, audio_id, patch.model_dump(include={"date"}))
    if patch.license is not MISSING:
        await tx.run(UPDATE_LICENSE_QUERY, id=audio_id, license=patch.license.value)
    if patch.language is not MISSING:
        language_code = await language.validated_language_code(tx, patch.language)
        await tx.run(UPDATE_LANGUAGE_QUERY, id=audio_id, bcp47=patch.language, language_code=language_code)
    if patch.title is not MISSING:
        await nomen.replace(tx, AUDIO_LABEL, audio_id, patch.title.root)
    if patch.contributions is not MISSING:
        await contribution.replace(tx, AUDIO_LABEL, audio_id, patch.contributions)
    return await get(tx, audio_id)


@writing
async def delete(tx: AsyncManagedTransaction, audio_id: str) -> None:
    await lock_nodes(tx, "Audio", [audio_id])
    not_found = f"Audio with ID '{audio_id}' not found"
    record = await fetch_record(tx, DELETE_CHECK_QUERY, not_found, audio_id=audio_id)
    raise_if_blockers(
        f"Audio '{audio_id}' cannot be deleted because it has", {"recording(s)": record["recording_count"]}
    )
    await tx.run(DELETE_QUERY, audio_id=audio_id)
