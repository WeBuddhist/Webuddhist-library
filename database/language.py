from typing import TYPE_CHECKING, LiteralString

from errors import DataValidationError, InvalidRequestError
from models.base import base_language_code
from models.responses import LanguageResponse

from .repository import (
    SessionSource,
    create_id,
    fetch_record,
    on_constraint_violation,
    query_all,
    raise_if_blockers,
    reading,
    writing,
)

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction

GET_ALL_QUERY: LiteralString = """
MATCH (l:Language)
RETURN l.code AS code, l.name AS name
ORDER BY l.code
"""
CREATE_QUERY: LiteralString = """
CREATE (l:Language {code: $code, name: $name})
RETURN l.code AS code
"""
DELETE_CHECK_QUERY: LiteralString = """
MATCH (l:Language {code: $code})
RETURN count { ()-[:HAS_LANGUAGE]->(l) } AS reference_count
"""
DELETE_QUERY: LiteralString = """
MATCH (l:Language {code: $code})
DELETE l
RETURN $code AS code
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


@reading
async def validated_language_code(tx: AsyncManagedTransaction, tag: str | None) -> str | None:
    if tag is None:
        return None
    code = base_language_code(tag)
    await validate_language_code_exists(tx, code)
    return code


async def _missing_language_codes(tx: AsyncManagedTransaction, codes: list[str]) -> tuple[list[str], list[str]]:
    """Return the codes with no Language node and, only when some are missing, every available code."""
    result = await tx.run(_MISSING_LANGUAGE_CODES_QUERY, codes=codes)
    record = await result.single(strict=True)
    return (record["missing"], record["available"])


async def validate_language_code_exists(tx: AsyncManagedTransaction, language_code: str) -> None:
    """Raise InvalidRequestError listing the available codes if the base language code does not exist."""
    missing, available = await _missing_language_codes(tx, [language_code])
    if not missing:
        return
    if not available:
        raise InvalidRequestError(f"Language '{language_code}' is not present in Neo4j. No languages found.")
    raise InvalidRequestError(
        f"Language '{language_code}' is not present in Neo4j. Available languages: {', '.join(available)}"
    )


async def validate_language_codes_exist(tx: AsyncManagedTransaction, language_codes: list[str]) -> None:
    """Validate that all given base language codes exist. Raises InvalidRequest listing missing and available."""
    missing, available = await _missing_language_codes(tx, [c.lower() for c in language_codes])
    if missing:
        raise InvalidRequestError(
            f"Languages {', '.join(missing)} are not present in Neo4j. Available languages: {', '.join(available)}"
        )


async def get_all(db: SessionSource) -> list[LanguageResponse]:
    return await query_all(db, GET_ALL_QUERY, LanguageResponse)


async def create(db: SessionSource, code: str, name: str) -> str:
    with on_constraint_violation(DataValidationError, f"Language with code '{code}' already exists"):
        return await db.write(create_id, CREATE_QUERY, "code", code=code, name=name)


@writing
async def delete(tx: AsyncManagedTransaction, code: str) -> None:
    not_found = f"Language with code '{code}' not found"
    record = await fetch_record(tx, DELETE_CHECK_QUERY, not_found, code=code)
    raise_if_blockers(
        f"Language '{code}' is referenced by", {"HAS_LANGUAGE relationship(s)": record["reference_count"]}
    )
    await tx.run(DELETE_QUERY, code=code)
