"""Repeatable schema upgrades. Run one at a time with writers paused; audit never mutates data."""

import logging
from enum import StrEnum
from pathlib import Path
from typing import Any, LiteralString, cast

from neo4j import AsyncDriver, AsyncManagedTransaction, AsyncSession

from database.neo4j_triggers import audit_triggers, install_triggers
from identifier import generate_id
from models.enums import (
    AUDIO_ROLES,
    AttributeType,
    BibliographyType,
    ContributorRole,
    EditionType,
    LicenseType,
    MarkType,
    NoteType,
)

logger = logging.getLogger(__name__)

AUDITS: dict[str, LiteralString] = {
    "duplicate_ids": """
        MATCH (n) WHERE n.id IS NOT NULL
        UNWIND labels(n) AS label
        WITH label, n.id AS id, count(*) AS count WHERE count > 1
        RETURN label + ':' + id AS violating_id
    """,
    "duplicate_lookup_names": """
        MATCH (n:AI|Language|Source|RoleType|EditionType|LicenseType|BibliographyType|NoteType|MarkType|AttributeType)
        UNWIND labels(n) AS label
        WITH label, coalesce(n.code, n.name, n.id) AS key, count(*) AS count
        WHERE key IS NOT NULL AND count > 1
        RETURN label + ':' + key AS violating_id
    """,
    "noncontiguous_segmentation": """
        MATCH (segmentation:Segmentation)<-[:SEGMENT_OF]-(segment:Segment)
        OPTIONAL MATCH (line:Span)-[:SPAN_OF]->(segment)
        WITH segmentation, segment, line ORDER BY line.start, line.end
        WITH segmentation, segment, collect(line) AS lines
        ORDER BY lines[0].start, lines[-1].end, segment.id
        WITH segmentation, collect({start: lines[0].start, end: lines[-1].end,
            invalid: size(lines) = 0 OR any(i IN range(1, size(lines) - 1)
                WHERE lines[i - 1].end <> lines[i].start)}) AS segments
        WHERE any(segment IN segments WHERE segment.invalid)
           OR any(i IN range(1, size(segments) - 1) WHERE segments[i - 1].end <> segments[i].start)
        RETURN coalesce(segmentation.id, elementId(segmentation)) AS violating_id
    """,
    "empty_pagination": """
        MATCH (n:Pagination|Volume|Page)
        WHERE (n:Pagination AND NOT (:Volume)-[:VOLUME_OF]->(n))
           OR (n:Volume AND NOT (:Page)-[:PAGE_OF]->(n))
           OR (n:Page AND NOT EXISTS { (s:Span)-[:SPAN_OF]->(n) WHERE s.start < s.end })
        RETURN coalesce(n.id, elementId(n)) AS violating_id
    """,
    "legacy_category_relationship": """
        MATCH (c:Category)-[:CHILD_OF]->() RETURN coalesce(c.id, elementId(c)) AS violating_id
    """,
    "recording_audio_key": """
        MATCH (r:Recording) WHERE r.audio_key IS NULL RETURN coalesce(r.id, elementId(r)) AS violating_id
    """,
}

# Each condition the conversion relies on; `migrate` refuses to run while a legacy recording fails one.
UNCONVERTIBLE_RECORDINGS_QUERY: LiteralString = """
    MATCH (r:Recording)-[:RECORDING_OF]->()
    WHERE NOT EXISTS {
            (r)-[:RECORDING_OF]->(:Edition)-[:EDITION_OF]->(t:Text)-[:TEXT_OF]->(:Work)
            WHERE EXISTS { (t)-[:HAS_LANGUAGE]->(:Language) }
        }
       OR count { (r)-[:RECORDING_OF]->() } > 1
       OR r.format IS NULL
       OR NOT EXISTS { (r)-[:HAS_CONTRIBUTION]->(:Contribution)-[:BY]->(:Person|AI) }
       OR EXISTS {
            (r)-[:HAS_CONTRIBUTION]->(:Contribution)-[:WITH_ROLE]->(role:RoleType)
            WHERE NOT coalesce(role.name, '') IN $roles
        }
       OR NOT (EXISTS { (r)-[:HAS_LICENSE]->(:LicenseType) } OR EXISTS { (:LicenseType {name: 'public'}) })
    RETURN DISTINCT r.id AS violating_id
"""

LEGACY_RECORDINGS_QUERY: LiteralString = """
    MATCH (r:Recording)-[:RECORDING_OF]->() RETURN DISTINCT r.id AS id ORDER BY id
"""

# A legacy recording was both performance and file, attached to one edition. Its performance metadata
# moves to a new Audio that recites the edition's text; the recording keeps its file and cues, and its
# edition link becomes FOLLOWS. Cue spans stay valid because they still point into the same edition.
UPGRADE_RECORDING_QUERY: LiteralString = """
    MATCH (r:Recording {id: $recording_id})-[legacy:RECORDING_OF]->(m:Edition)
          -[:EDITION_OF]->(t:Text)-[:TEXT_OF]->(w:Work)
    WHERE NOT (r)-[:RENDITION_OF]->()
    MATCH (t)-[text_language_rel:HAS_LANGUAGE]->(text_language:Language)
    OPTIONAL MATCH (r)-[language_rel:HAS_LANGUAGE]->(language:Language)
    OPTIONAL MATCH (r)-[license_rel:HAS_LICENSE]->(license:LicenseType)
    OPTIONAL MATCH (public:LicenseType {name: 'public'})
    OPTIONAL MATCH (r)-[title_rel:HAS_TITLE]->(title:Nomen)
    WITH r, legacy, m, t, w, title, title_rel, language_rel, license_rel,
         coalesce(language, text_language) AS audio_language,
         CASE WHEN language IS NULL THEN coalesce(text_language_rel.bcp47, text_language.code)
              ELSE coalesce(language_rel.bcp47, language.code) END AS bcp47,
         coalesce(license, public) AS audio_license
    CREATE (a:Audio {id: $audio_id, date: r.date})
    CREATE (a)-[:AUDIO_OF]->(w), (a)-[:RECITATION_OF]->(t),
           (a)-[:HAS_LANGUAGE {bcp47: bcp47}]->(audio_language), (a)-[:HAS_LICENSE]->(audio_license)
    CALL (*) { WHEN title IS NOT NULL THEN { CREATE (a)-[:HAS_TITLE]->(title) } }
    DELETE title_rel, language_rel, license_rel
    WITH r, legacy, m, a
    CALL (r, a) {
        MATCH (r)-[contribution_rel:HAS_CONTRIBUTION]->(c:Contribution)
        CREATE (a)-[:HAS_CONTRIBUTION]->(c)
        DELETE contribution_rel
    }
    CREATE (r)-[:RENDITION_OF]->(a), (r)-[:FOLLOWS]->(m)
    DELETE legacy
    SET r.audio_key = coalesce(r.audio_key, 'recordings/' + m.id + '/' + r.id + '.' + r.format)
    REMOVE r.date
    RETURN a.id AS audio_id
"""


async def _ids(
    runner: AsyncSession | AsyncManagedTransaction, query: LiteralString, parameters: dict[str, Any] | None = None
) -> list[str]:
    result = await runner.run(query, parameters)
    return [record.value() async for record in result]


async def legacy_recording_ids(driver: AsyncDriver, database: str) -> list[str]:
    """Recordings still attached to an edition, waiting for `migrate`."""
    async with driver.session(database=database) as session:
        return await _ids(session, LEGACY_RECORDINGS_QUERY)


async def unconvertible_recording_ids(driver: AsyncDriver, database: str) -> list[str]:
    async with driver.session(database=database) as session:
        return await _ids(session, UNCONVERTIBLE_RECORDINGS_QUERY, {"roles": list(AUDIO_ROLES)})


async def audit_schema(driver: AsyncDriver, database: str) -> dict[str, list[str]]:
    """Report invariant violations. Legacy recordings are pending conversion, unless they cannot be converted."""
    violations = await audit_triggers(driver, database)
    async with driver.session(database=database) as session:
        for name, query in AUDITS.items():
            result = await session.run(query)
            ids = [record["violating_id"] async for record in result]
            if ids:
                violations[name] = ids
    legacy = set(await legacy_recording_ids(driver, database))
    if legacy:
        violations = {name: kept for name, ids in violations.items() if (kept := [i for i in ids if i not in legacy])}
    if unconvertible := await unconvertible_recording_ids(driver, database):
        violations["legacy_recording_unconvertible"] = unconvertible
    return violations


async def upgrade_recordings(driver: AsyncDriver, database: str) -> int:
    """Convert every legacy recording into an Audio plus its Recording, all or none; return the count.

    `migrate` audits for unconvertible recordings first. The current triggers must be live, because the ones
    they replace reject a Recording without RECORDING_OF.
    """

    async def convert(tx: AsyncManagedTransaction) -> int:
        pending = await _ids(tx, LEGACY_RECORDINGS_QUERY)
        for recording_id in pending:
            result = await tx.run(UPGRADE_RECORDING_QUERY, recording_id=recording_id, audio_id=generate_id())
            await result.single(strict=True)
        return len(pending)

    async with driver.session(database=database) as session:
        converted = await session.execute_write(convert)
    logger.info("Converted %d legacy recording(s)", converted)
    return converted


async def migrate(driver: AsyncDriver, database: str) -> None:
    """Audit and apply every idempotent step on each explicit invocation."""
    async with driver.session(database="system") as system:
        await (await system.run("ALTER DATABASE $database SET DEFAULT LANGUAGE CYPHER 25", database=database)).consume()

    violations = await audit_schema(driver, database)
    if violations:
        for name, ids in violations.items():
            logger.error("%s: %s", name, ids[:20])
        raise RuntimeError("Schema audit failed; repair reported records before applying migrations")
    async with driver.session(database=database) as session:
        constraints = Path(__file__).with_name("neo4j_constraints.cypher").read_text()
        for statement in constraints.split(";"):
            if statement.strip():
                await (await session.run(cast(LiteralString, statement))).consume()
        lookups: dict[LiteralString, type[StrEnum]] = {
            "RoleType": ContributorRole,
            "EditionType": EditionType,
            "LicenseType": LicenseType,
            "NoteType": NoteType,
            "MarkType": MarkType,
            "BibliographyType": BibliographyType,
            "AttributeType": AttributeType,
        }
        for label, enum in lookups.items():
            await (
                await session.run(f"UNWIND $names AS name MERGE (:{label} {{name: name}})", names=list(enum))
            ).consume()
    await install_triggers(driver, database)
    await upgrade_recordings(driver, database)
