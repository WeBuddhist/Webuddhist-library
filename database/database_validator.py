import logging
from collections.abc import Sequence
from typing import LiteralString

from neo4j import AsyncManagedTransaction

from exceptions import DataNotFoundError, DataValidationError, ForbiddenError, InvalidRequestError
from models.base import base_language_code
from models.contribution import ContributionInputItem, PersonContributionInput
from models.text import TextInput

from .repository import require_node

logger = logging.getLogger(__name__)

_MISSING_PERSON_IDS_QUERY: LiteralString = """
MATCH (p:Person) WHERE p.id IN $references
WITH collect(DISTINCT p.id) AS found
RETURN [x IN $references WHERE NOT x IN found] AS missing
"""

_MISSING_PERSON_BDRC_IDS_QUERY: LiteralString = """
MATCH (p:Person) WHERE p.bdrc IN $references
WITH collect(DISTINCT p.bdrc) AS found
RETURN [x IN $references WHERE NOT x IN found] AS missing
"""

_MISSING_LANGUAGE_CODES_QUERY: LiteralString = """
UNWIND $codes AS code
OPTIONAL MATCH (l:Language {code: code})
WITH collect(DISTINCT CASE WHEN l IS NULL THEN code END) AS missing
RETURN missing,
       CASE WHEN size(missing) > 0
         THEN COLLECT { MATCH (lang:Language) WITH lang ORDER BY lang.code RETURN lang.code }
         ELSE []
       END AS available
"""


class DatabaseValidator:
    @staticmethod
    async def validate_contribution_references(
        tx: AsyncManagedTransaction, contributions: Sequence[ContributionInputItem]
    ) -> None:
        persons = [contrib for contrib in contributions if isinstance(contrib, PersonContributionInput)]
        ids = [person.id for person in persons if person.id]
        bdrc_ids = [person.bdrc_id for person in persons if person.bdrc_id]

        checks: tuple[tuple[LiteralString, str, list[str]], ...] = (
            (_MISSING_PERSON_IDS_QUERY, "persons", ids),
            (_MISSING_PERSON_BDRC_IDS_QUERY, "person BDRC IDs", bdrc_ids),
        )

        for query, label, references in checks:
            if not references:
                continue

            result = await tx.run(query, references=references)
            missing = (await result.single(strict=True))["missing"]
            if missing:
                raise DataValidationError(f"Referenced {label} do not exist: {', '.join(missing)}")

    @staticmethod
    async def validate_text_creation(tx: AsyncManagedTransaction, text: TextInput) -> None:
        await DatabaseValidator.validate_text_title_unique(tx, dict(text.title.root), text.language)

        await DatabaseValidator.validate_contribution_references(tx, text.contributions)

    @staticmethod
    async def validate_text_exists(tx: AsyncManagedTransaction, text_id: str) -> None:
        error = DataValidationError(f"Text {text_id} does not exist. Cannot create edition for non-existent text.")
        await require_node(tx, "Text", text_id, error)

    @staticmethod
    async def validate_edition_exists(tx: AsyncManagedTransaction, edition_id: str) -> None:
        await require_node(tx, "Edition", edition_id, DataNotFoundError(f"Edition with ID '{edition_id}' not found"))

    @staticmethod
    async def validated_language_code(tx: AsyncManagedTransaction, tag: str | None) -> str | None:
        if tag is None:
            return None
        code = base_language_code(tag)
        await DatabaseValidator.validate_language_code_exists(tx, code)
        return code

    @staticmethod
    async def _missing_language_codes(tx: AsyncManagedTransaction, codes: list[str]) -> tuple[list[str], list[str]]:
        """Return the codes with no Language node and, only when some are missing, every available code."""
        result = await tx.run(_MISSING_LANGUAGE_CODES_QUERY, codes=codes)
        record = await result.single(strict=True)
        return record["missing"], record["available"]

    @staticmethod
    async def validate_language_code_exists(tx: AsyncManagedTransaction, language_code: str) -> None:
        """Raise InvalidRequestError listing the available codes if the base language code does not exist."""
        missing, available = await DatabaseValidator._missing_language_codes(tx, [language_code])
        if not missing:
            return
        if not available:
            raise InvalidRequestError(f"Language '{language_code}' is not present in Neo4j. No languages found.")
        raise InvalidRequestError(
            f"Language '{language_code}' is not present in Neo4j. Available languages: {', '.join(available)}"
        )

    @staticmethod
    async def validate_language_codes_exist(tx: AsyncManagedTransaction, language_codes: list[str]) -> None:
        """Validate that all given base language codes exist. Raises InvalidRequest listing missing and available."""
        missing, available = await DatabaseValidator._missing_language_codes(tx, [c.lower() for c in language_codes])
        if missing:
            raise InvalidRequestError(
                f"Languages {', '.join(missing)} are not present in Neo4j. Available languages: {', '.join(available)}"
            )

    @staticmethod
    async def validate_category_exists(
        tx: AsyncManagedTransaction, category_id: str, application: str | None = None
    ) -> None:
        """Validate that a category with the given ID exists.

        Raises DataValidationError if the category does not exist.
        """
        query = """
        MATCH (c:Category {id: $category_id})-[:BELONGS_TO]->(app:Application)
        RETURN app.id AS application
        """

        result = await tx.run(query, category_id=category_id)
        record = await result.single()

        if not record:
            raise DataValidationError(
                f"Category with ID '{category_id}' does not exist. Please provide a valid category_id."
            )
        if application is not None and record["application"] != application:
            raise ForbiddenError("Category belongs to another application")

    @staticmethod
    async def validate_tags_exist(
        tx: AsyncManagedTransaction, tag_ids: list[str], application: str | None = None
    ) -> None:
        """Validate that all given tag IDs exist. Raises DataValidationError listing missing IDs."""
        if not tag_ids:
            return

        query = """
        MATCH (t:Tag) WHERE t.id IN $tag_ids
        WITH collect(DISTINCT t) AS tags
        RETURN [x IN $tag_ids WHERE NOT x IN [t IN tags | t.id]] AS missing_tags,
               [t IN tags WHERE $application IS NOT NULL AND NOT
                   (t)-[:BELONGS_TO]->(:Application {id: $application}) | t.id] AS foreign_tags
        """

        result = await tx.run(query, tag_ids=tag_ids, application=application)
        record = await result.single(strict=True)
        if record["missing_tags"]:
            raise DataValidationError(f"Referenced tags do not exist: {', '.join(record['missing_tags'])}")
        if record["foreign_tags"]:
            raise ForbiddenError("Tags belong to another application")

    @staticmethod
    async def validate_text_title_unique(
        tx: AsyncManagedTransaction, title: dict[str, str], text_language: str
    ) -> None:
        """Ensure no text in the same language has the same title text and language combination."""
        base_language = base_language_code(text_language)
        titles_list = [
            {"text": text, "lang": lang} for lang, text in title.items() if base_language_code(lang) == base_language
        ]
        if not titles_list:
            return

        query = """
        UNWIND $titles AS item
        MATCH (lang:Language)<-[:HAS_LANGUAGE]-(:Text)-[:HAS_TITLE]->(:Nomen)
            -[:HAS_LOCALIZATION]->(lt:LocalizedText {text: item.text})-[rel:HAS_LANGUAGE]->(lang)
        WHERE toLower(coalesce(rel.bcp47, lang.code)) = toLower(item.lang)
        RETURN item.text AS text, item.lang AS lang
        LIMIT 1
        """

        result = await tx.run(query, titles=titles_list)
        record = await result.single()

        if record:
            raise DataValidationError(
                f"Text with title '{record['text']}' in language '{record['lang']}' already exists"
            )
