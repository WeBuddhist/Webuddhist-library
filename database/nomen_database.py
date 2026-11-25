from typing import TYPE_CHECKING, LiteralString

from identifier import generate_id
from models.base import base_language_code

from .database_validator import DatabaseValidator

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction


class NomenDatabase:
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

    @staticmethod
    def localized_map(nomen_path: LiteralString) -> LiteralString:
        """Project a Cypher path ending at a Nomen into a language-to-text map."""
        return f"""apoc.map.fromPairs([{nomen_path}-[:HAS_LOCALIZATION]->(loc_text:LocalizedText)
            -[loc_rel:HAS_LANGUAGE]->(loc_lang:Language) | [coalesce(loc_rel.bcp47, loc_lang.code), loc_text.text]])"""

    @staticmethod
    def optional_localized_map(nomen_path: LiteralString) -> LiteralString:
        """Return null when the path has no LocalizedText node."""
        return f"""CASE WHEN EXISTS {{ {nomen_path}-[:HAS_LOCALIZATION]->(:LocalizedText) }}
            THEN {NomenDatabase.localized_map(nomen_path)} END"""

    @staticmethod
    def alternative_maps(nomen_path: LiteralString) -> LiteralString:
        return f"""[{nomen_path}<-[:ALTERNATIVE_OF]-(alt_nomen:Nomen) |
            {NomenDatabase.localized_map("(alt_nomen)")}]"""

    @staticmethod
    async def create_many_with_transaction(tx: AsyncManagedTransaction, nomens: dict[str, dict[str, str]]) -> None:
        base_codes = {tag: base_language_code(tag) for texts in nomens.values() for tag in texts}
        await DatabaseValidator.validate_language_codes_exist(tx, sorted(set(base_codes.values())))
        rows = [{"id": name_id, "translations": list(texts.items())} for name_id, texts in nomens.items()]
        result = await tx.run(NomenDatabase.CREATE_MANY_QUERY, nomens=rows, base_codes=base_codes)
        if set(await result.value("id")) != set(nomens):
            raise RuntimeError("Incomplete nomen creation")

    @staticmethod
    async def create_with_transaction(
        tx: AsyncManagedTransaction, primary_text: dict[str, str], alternative_texts: list[dict[str, str]] | None = None
    ) -> str:
        primary_id = generate_id()
        alternatives = {generate_id(): texts for texts in alternative_texts or []}
        await NomenDatabase.create_many_with_transaction(tx, {primary_id: primary_text, **alternatives})
        if alternatives:
            result = await tx.run(
                NomenDatabase.LINK_ALTERNATIVES_QUERY,
                primary_id=primary_id,
                alternative_ids=list(alternatives),
            )
            await result.consume()
        return primary_id

    @classmethod
    async def replace_with_transaction(
        cls,
        tx: AsyncManagedTransaction,
        owner_label: LiteralString,
        owner_id: str,
        primary_text: dict[str, str],
        *,
        relationship: LiteralString = "HAS_TITLE",
        alternative_texts: list[dict[str, str]] | None = None,
    ) -> None:
        """Replace a locked owner's name. Pass a list for fields that own aliases, including [] to clear them."""
        nomen_id = await cls.create_with_transaction(tx, primary_text, alternative_texts)
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
