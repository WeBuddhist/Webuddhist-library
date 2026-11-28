"""Repeatable schema upgrades. Run one at a time with writers paused; audit never mutates data."""

import logging
from enum import StrEnum
from typing import LiteralString, cast

from neo4j import AsyncDriver, AsyncManagedTransaction, AsyncResult, RoutingControl

from database.neo4j_triggers import audit_triggers, install_triggers
from database.schema import PROPERTY_INDEXES, UNIQUE_PROPERTIES, schema_statements
from identifier import generate_id
from models.enums import (
    AUDIO_ROLES,
    AttributeType,
    AudioFormat,
    BibliographyType,
    ContributorRole,
    EditionType,
    LicenseType,
    MarkType,
    NoteType,
    SegmentType,
)

logger = logging.getLogger(__name__)

LOOKUP_ENUMS: dict[LiteralString, type[StrEnum]] = {
    "RoleType": ContributorRole,
    "EditionType": EditionType,
    "LicenseType": LicenseType,
    "NoteType": NoteType,
    "MarkType": MarkType,
    "BibliographyType": BibliographyType,
    "AttributeType": AttributeType,
}

AUDITS: dict[str, LiteralString] = {
    "enforce_excerpt_edition": """
        MATCH (e:Edition)
        WHERE (e.content_key IS NULL) <> EXISTS { (e)-[:HAS_TYPE]->(:EditionType {name: 'excerpt'}) }
        RETURN coalesce(toStringOrNull(e.id), elementId(e)) AS violating_id
    """,
    "noncontiguous_segmentation": """
        MATCH (segmentation:Segmentation)<-[:SEGMENT_OF]-(segment:Segment)
        MATCH (line:Span)-[:SPAN_OF]->(segment)
        WITH segmentation, segment, line ORDER BY line.start, line.end
        WITH segmentation, segment, collect(line) AS lines
        ORDER BY lines[0].start, lines[-1].end, segment.id
        WITH segmentation, collect({start: lines[0].start, end: lines[-1].end,
            invalid: any(i IN range(1, size(lines) - 1)
                WHERE lines[i - 1].end <> lines[i].start)}) AS segments
        WHERE any(segment IN segments WHERE segment.invalid)
           OR any(i IN range(1, size(segments) - 1) WHERE segments[i - 1].end <> segments[i].start)
        RETURN coalesce(segmentation.id, elementId(segmentation)) AS violating_id
    """,
    "empty_pagination": """
        MATCH (n:Page)
        WHERE NOT EXISTS { (s:Span)-[:SPAN_OF]->(n) WHERE s.start < s.end }
        RETURN coalesce(n.id, elementId(n)) AS violating_id
    """,
    "legacy_category_relationship": """
        MATCH (c:Category)-[:CHILD_OF]->() RETURN coalesce(c.id, elementId(c)) AS violating_id
    """,
}

# Each condition the conversion relies on; `migrate` refuses to run while a legacy recording fails one.
UNCONVERTIBLE_RECORDINGS_QUERY: LiteralString = """
    MATCH (r:Recording)-[:RECORDING_OF]->()
    WHERE NOT EXISTS {
            (r)-[:RECORDING_OF]->(:Edition)-[:EDITION_OF]->(t:Text)
            WHERE EXISTS { (t)-[:HAS_LANGUAGE]->(:Language) }
        }
       OR count { (r)-[:RECORDING_OF]->() } > 1
       OR EXISTS { (r)-[:RENDITION_OF]->() }
       OR EXISTS { (r)-[:FOLLOWS]->(edition) WHERE NOT (r)-[:RECORDING_OF]->(edition) }
       OR count { (r)-[:HAS_LANGUAGE]->() } > 1
       OR count { (r)-[:HAS_LICENSE]->() } > 1
       OR NOT (r.date IS :: STRING)
       OR EXISTS { (r)-[lang:HAS_LANGUAGE]->() WHERE NOT (lang.bcp47 IS :: STRING) }
       OR r.format IS NULL
       OR NOT EXISTS { (r)-[:HAS_CONTRIBUTION]->(:Contribution)-[:BY]->(:Person|AI) }
       OR EXISTS {
            (r)-[:HAS_CONTRIBUTION]->(:Contribution)-[:WITH_ROLE]->(role:RoleType)
            WHERE NOT coalesce(role.name, '') IN $roles
        }
       OR EXISTS {
            (r)-[:HAS_CONTRIBUTION]->(c:Contribution) WHERE count { ()-[:HAS_CONTRIBUTION]->(c) } > 1
        }
       OR EXISTS { (r)-[:HAS_TITLE]->(:Nomen)<-[:HAS_TITLE]-(:Audio) }
    RETURN DISTINCT coalesce(toStringOrNull(r.id), elementId(r)) AS violating_id
"""

LEGACY_RECORDINGS_QUERY: LiteralString = """
    MATCH (r:Recording)-[:RECORDING_OF]->() RETURN DISTINCT r.id AS id ORDER BY id
"""

# Only omit failures on the actual nodes and edges that conversion repairs. IDs are unique per label.
_LEGACY = "EXISTS { (node:Recording)-[:RECORDING_OF]->(:Edition) }"
_RECORDING_REPAIRS = {
    "enforce_recording_follows": f"failure.rule = 'enforce_recording_follows' AND {_LEGACY}",
    "enforce_recording_rendition_of": f"failure.rule = 'enforce_recording_rendition_of' AND {_LEGACY}",
    "enforce_recording_properties": (
        f"failure.rule = 'enforce_recording_audio_key_property' AND node.audio_key IS NULL AND {_LEGACY}"
    ),
    "enforce_recording_follows_recited_text": "EXISTS { (node)-[:RECORDING_OF]->(edition) }",
    **{
        f"enforce_{rel}_target": f"EXISTS {{ (source:Recording)-[:RECORDING_OF]->(:Edition) }} AND target:{target}"
        for rel, target in (
            ("has_contribution", "Contribution"),
            ("has_language", "Language"),
            ("has_license", "LicenseType"),
        )
    },
}

# A legacy recording was both performance and file, attached to one edition. Its performance metadata
# moves to a new Audio that recites the edition's text; the recording keeps its file and cues, and its
# edition link becomes FOLLOWS. Cue spans stay valid because they still point into the same edition.
UPGRADE_RECORDING_QUERY: LiteralString = """
    MATCH (r:Recording {id: $recording_id})-[legacy:RECORDING_OF]->(m:Edition)
          -[:EDITION_OF]->(t:Text)
    WHERE NOT (r)-[:RENDITION_OF]->()
    MATCH (t)-[text_language_rel:HAS_LANGUAGE]->(text_language:Language)
    OPTIONAL MATCH (r)-[language_rel:HAS_LANGUAGE]->(language:Language)
    OPTIONAL MATCH (r)-[license_rel:HAS_LICENSE]->(license:LicenseType)
    OPTIONAL MATCH (public:LicenseType {name: 'public'})
    OPTIONAL MATCH (r)-[title_rel:HAS_TITLE]->(title:Nomen)
    WITH r, legacy, m, t, title, title_rel, language_rel, license_rel,
         coalesce(language, text_language) AS audio_language,
         CASE WHEN language IS NULL THEN coalesce(text_language_rel.bcp47, text_language.code)
              ELSE coalesce(language_rel.bcp47, language.code) END AS bcp47,
         coalesce(license, public) AS audio_license
    CREATE (a:Audio {id: $audio_id, date: r.date})
    CREATE (a)-[:RECITATION_OF]->(t),
           (a)-[:HAS_LANGUAGE {bcp47: bcp47}]->(audio_language), (a)-[:HAS_LICENSE]->(audio_license)
    CALL (*) { WHEN title IS NOT NULL THEN { CREATE (a)-[:HAS_TITLE]->(title) } }
    DELETE title_rel, language_rel, license_rel
    WITH r, legacy, m, a
    CALL (r, a) {
        MATCH (r)-[contribution_rel:HAS_CONTRIBUTION]->(c:Contribution)
        CREATE (a)-[:HAS_CONTRIBUTION]->(c)
        DELETE contribution_rel
    }
    CREATE (r)-[:RENDITION_OF]->(a)
    MERGE (r)-[:FOLLOWS]->(m)
    DELETE legacy
    SET r.audio_key = coalesce(r.audio_key, 'recordings/' + m.id + '/' + r.id + '.' + r.format)
    REMOVE r.date
    RETURN a.id AS audio_id
"""


async def _ids(driver: AsyncDriver, database: str, query: LiteralString, **params: object) -> list[str]:
    """The first column of every row."""
    return await driver.execute_query(
        query, params, database_=database, routing_=RoutingControl.READ, result_transformer_=AsyncResult.value
    )


async def legacy_recording_ids(driver: AsyncDriver, database: str) -> list[str]:
    """Recordings still attached to an edition, waiting for `migrate`."""
    return await _ids(driver, database, LEGACY_RECORDINGS_QUERY)


async def unconvertible_recording_ids(driver: AsyncDriver, database: str) -> list[str]:
    return await _ids(driver, database, UNCONVERTIBLE_RECORDINGS_QUERY, roles=list(AUDIO_ROLES))


async def audit_schema(driver: AsyncDriver, database: str, *, for_migration: bool = False) -> dict[str, list[str]]:
    """Audit the data, independently of which constraints and triggers are installed."""
    violations = await audit_triggers(driver, database, exclusions=_RECORDING_REPAIRS if for_migration else None)
    for name, (label, prop) in UNIQUE_PROPERTIES.items():
        # Report node identifiers, including for sensitive unique properties such as API key hashes.
        duplicates = f"""
            MATCH (n:{label}) WHERE n.{prop} IS NOT NULL
            WITH n.{prop} AS value, count(*) AS copies WHERE copies > 1
            MATCH (duplicate:{label} {{{prop}: value}})
            RETURN elementId(duplicate) AS violating_id
        """
        if ids := await _ids(driver, database, cast(LiteralString, duplicates)):
            violations[name] = ids
    enum_fields = {(label, "name"): enum for label, enum in LOOKUP_ENUMS.items()}
    enum_fields.update({("Recording", "format"): AudioFormat, ("Segment", "type"): SegmentType})
    for (label, prop), enum in enum_fields.items():
        query = """
            MATCH (n:$($label))
            WHERE n[$prop] IS :: STRING NOT NULL AND NOT n[$prop] IN $allowed
            RETURN coalesce(toStringOrNull(n.id), elementId(n)) AS violating_id
        """
        if ids := await _ids(driver, database, query, label=label, prop=prop, allowed=list(enum)):
            violations[f"{label}.{prop}.enum"] = ids
    for name, query in AUDITS.items():
        if ids := await _ids(driver, database, query):
            violations[name] = ids
    if unconvertible := await unconvertible_recording_ids(driver, database):
        violations["legacy_recording_unconvertible"] = unconvertible
    return violations


async def upgrade_recordings(driver: AsyncDriver, database: str) -> int:
    """Convert every legacy recording into an Audio plus its Recording, all or none; return the count.

    `migrate` audits for unconvertible recordings first. The current triggers must be live, because the ones
    they replace reject a Recording without RECORDING_OF.
    """

    async def convert(tx: AsyncManagedTransaction) -> int:
        result = await tx.run(LEGACY_RECORDINGS_QUERY)
        pending = await result.value()
        for recording_id in pending:
            result = await tx.run(UPGRADE_RECORDING_QUERY, recording_id=recording_id, audio_id=generate_id())
            await result.single(strict=True)
        return len(pending)

    async with driver.session(database=database) as session:
        converted = await session.execute_write(convert)
    logger.info("Converted %d legacy recording(s)", converted)
    return converted


async def install_constraints(driver: AsyncDriver, database: str) -> None:
    """Apply the unique constraints and property indexes declared in neo4j_schema.yaml."""
    # The Source.name uniqueness constraint supplies its own range index on the same schema.
    range_indexes = """
        SHOW RANGE INDEXES YIELD name, entityType, labelsOrTypes, properties, owningConstraint
        WHERE entityType = 'NODE' AND size(properties) = 1
    """
    legacy = "AND labelsOrTypes = ['Source'] AND properties = ['name'] AND owningConstraint IS NULL RETURN name"
    for name in await _ids(driver, database, range_indexes + legacy):
        await driver.execute_query("DROP INDEX $name IF EXISTS", name=name, database_=database)
    for statement in schema_statements():
        await driver.execute_query(cast(LiteralString, statement), database_=database)
    # IF NOT EXISTS also succeeds when a name is already in use on a different schema.
    indexes, _, _ = await driver.execute_query(
        range_indexes + "RETURN labelsOrTypes[0] AS label, properties[0] AS prop, name, owningConstraint",
        database_=database,
    )
    unique = {(row["label"], row["prop"]) for row in indexes if row["owningConstraint"]}
    if missing := set(UNIQUE_PROPERTIES.values()) - unique:
        raise RuntimeError(f"Missing uniqueness constraints after installation: {sorted(missing)}")
    names = {(row["label"], row["prop"]): row["name"] for row in indexes}
    for label, prop in PROPERTY_INDEXES.values():
        if (label, prop) not in names:
            raise RuntimeError(f"Missing range index for {label}.{prop} after installation")
        await driver.execute_query("CALL db.awaitIndex($name)", name=names[label, prop], database_=database)


async def migrate(driver: AsyncDriver, database: str) -> None:
    """Audit and apply every idempotent step on each explicit invocation."""
    await driver.execute_query(
        "ALTER DATABASE $database SET DEFAULT LANGUAGE CYPHER 25", database=database, database_="system"
    )

    violations = await audit_schema(driver, database, for_migration=True)
    if violations:
        for name, ids in violations.items():
            logger.error("%s: %s", name, ids[:20])
        raise RuntimeError("Schema audit failed; repair reported records before applying migrations")
    # An equivalent constraint under its old name makes CREATE ... IF NOT EXISTS skip the new name.
    for name in ("api_key_hash_unique", "bibliographic_id_unique", "toc_id_unique", "toc_section_id_unique"):
        await driver.execute_query("DROP CONSTRAINT $name IF EXISTS", name=name, database_=database)
    await install_constraints(driver, database)
    await install_triggers(driver, database)
    for label, enum in LOOKUP_ENUMS.items():
        query = f"UNWIND $names AS name MERGE (:{label} {{name: name}})"
        await driver.execute_query(query, names=list(enum), database_=database)
    # The old triggers required this edge. The recited text now supplies the Work.
    await driver.execute_query("MATCH ()-[rel:AUDIO_OF]->() DELETE rel", database_=database)
    await upgrade_recordings(driver, database)
