from typing import TYPE_CHECKING, LiteralString

from database import language
from identifier import generate_id
from models.base import base_language_code

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction

CREATE_MANY_QUERY: LiteralString = """
UNWIND $nomens AS data
CREATE (n:Nomen {id: data.id})
CALL (n, data) {
    UNWIND data.translations AS pair
    WITH n, pair[0] AS tag, pair[1] AS text
    MATCH (l:Language {code: $base_codes[tag]})
    CREATE (n)-[:HAS_LOCALIZATION]->(:LocalizedText {text: text})
        -[:HAS_LANGUAGE {bcp47: tag}]->(l)
}
RETURN n.id AS id
"""
LINK_ALTERNATIVES_QUERY: LiteralString = """
MATCH (primary:Nomen {id: $primary_id})
UNWIND $alternative_ids AS alternative_id
MATCH (alternative:Nomen {id: alternative_id})
CREATE (alternative)-[:ALTERNATIVE_OF]->(primary)
FINISH
"""


def localized_map(nomen_path: LiteralString) -> LiteralString:
    """Project a Cypher path ending at a Nomen into a language-to-text map."""
    return f"""apoc.map.fromPairs([{nomen_path}-[:HAS_LOCALIZATION]->(loc_text:LocalizedText)
        -[loc_rel:HAS_LANGUAGE]->(loc_lang:Language) | [coalesce(loc_rel.bcp47, loc_lang.code), loc_text.text]])"""


def optional_localized_map(nomen_path: LiteralString) -> LiteralString:
    """Return null when the path has no LocalizedText node."""
    return f"""CASE WHEN EXISTS {{ {nomen_path}-[:HAS_LOCALIZATION]->(:LocalizedText) }}
        THEN {localized_map(nomen_path)} END"""


def delete_names(owner: LiteralString, *relationships: LiteralString) -> LiteralString:
    """Delete the owner's names over the relationships, with their localizations and alternative names."""
    return f"""
    CALL ({owner}) {{
        OPTIONAL MATCH ({owner})-[:{"|".join(relationships)}]->(deleted_name:Nomen)
        OPTIONAL MATCH (deleted_name)-[:HAS_LOCALIZATION]->(deleted_text:LocalizedText)
        OPTIONAL MATCH (deleted_name)<-[:ALTERNATIVE_OF]-(deleted_alternative:Nomen)
        OPTIONAL MATCH (deleted_alternative)-[:HAS_LOCALIZATION]->(deleted_alternative_text:LocalizedText)
        DETACH DELETE deleted_name, deleted_text, deleted_alternative, deleted_alternative_text
    }}
    """


def alternative_maps(nomen_path: LiteralString) -> LiteralString:
    return f"""[{nomen_path}<-[:ALTERNATIVE_OF]-(alt_nomen:Nomen) |
        {localized_map("(alt_nomen)")}]"""


async def create_many(tx: AsyncManagedTransaction, nomens: dict[str, dict[str, str]]) -> None:
    base_codes = {tag: base_language_code(tag) for texts in nomens.values() for tag in texts}
    await language.validate_language_codes_exist(tx, sorted(set(base_codes.values())))
    rows = [{"id": name_id, "translations": list(texts.items())} for name_id, texts in nomens.items()]
    result = await tx.run(CREATE_MANY_QUERY, nomens=rows, base_codes=base_codes)
    if set(await result.value("id")) != set(nomens):
        raise RuntimeError("Incomplete nomen creation")


async def create(
    tx: AsyncManagedTransaction, primary_text: dict[str, str], alternative_texts: list[dict[str, str]] | None = None
) -> str:
    primary_id = generate_id()
    alternatives = {generate_id(): texts for texts in alternative_texts or []}
    await create_many(tx, {primary_id: primary_text, **alternatives})
    if alternatives:
        result = await tx.run(LINK_ALTERNATIVES_QUERY, primary_id=primary_id, alternative_ids=list(alternatives))
        await result.consume()
    return primary_id


async def create_optional(
    tx: AsyncManagedTransaction,
    primary_text: dict[str, str] | None,
    alternative_texts: list[dict[str, str]] | None = None,
) -> str | None:
    return None if primary_text is None else await create(tx, primary_text, alternative_texts)


async def replace(
    tx: AsyncManagedTransaction,
    owner_label: LiteralString,
    owner_id: str,
    primary_text: dict[str, str],
    *,
    relationship: LiteralString = "HAS_TITLE",
    alternative_texts: list[dict[str, str]] | None = None,
) -> None:
    """Replace a locked owner's name. Pass a list for fields that own aliases, including [] to clear them."""
    nomen_id = await create(tx, primary_text, alternative_texts)
    await tx.run(
        f"""
        MATCH (owner:{owner_label} {{id: $owner_id}}), (replacement:Nomen {{id: $nomen_id}})
        CALL (owner) {{
            MATCH (owner)-[:{relationship}]->(old:Nomen)
            OPTIONAL MATCH (old)-[:HAS_LOCALIZATION]->(lt:LocalizedText)
            OPTIONAL MATCH (old)<-[:ALTERNATIVE_OF]-(alt:Nomen)-[:HAS_LOCALIZATION]->(alt_lt:LocalizedText)
            WHERE $replace_alternatives
            DETACH DELETE old, lt, alt, alt_lt
        }}
        CREATE (owner)-[:{relationship}]->(replacement)
        FINISH
        """,
        owner_id=owner_id,
        nomen_id=nomen_id,
        replace_alternatives=alternative_texts is not None,
    )
