import asyncio
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal, LiteralString, cast

from neo4j import AsyncDriver, AsyncManagedTransaction

from database.schema import NODES
from models.enums import AUDIO_ROLES

logger = logging.getLogger(__name__)

DATABASE_NAME = "neo4j"
SYSTEM_DATABASE_NAME = "system"

# APOC supplies changed properties and labels separately from created elements. Bind the surviving
# created or changed elements once per statement, so updates use the same rules as creation.
# Hash-set subtraction also dedupes; `IN` filters are quadratic on large changes.
_CHANGED_RELATIONSHIPS = """
WITH apoc.coll.subtract($createdRelationships
    + coll.flatten([key IN keys($assignedRelationshipProperties) |
        [entry IN $assignedRelationshipProperties[key] | entry.relationship]])
    + coll.flatten([key IN keys($removedRelationshipProperties) |
        [entry IN $removedRelationshipProperties[key] | entry.relationship]]),
    $deletedRelationships) AS changedRelationships
"""
_CHANGED_NODES = """
WITH changedRelationships, apoc.coll.subtract($createdNodes
    + coll.flatten([key IN keys($assignedNodeProperties) | [entry IN $assignedNodeProperties[key] | entry.node]])
    + coll.flatten([key IN keys($removedNodeProperties) | [entry IN $removedNodeProperties[key] | entry.node]])
    + coll.flatten([label IN keys($assignedLabels) | $assignedLabels[label]])
    + coll.flatten([label IN keys($removedLabels) | $removedLabels[label]
        + coll.flatten([node IN $removedLabels[label] WHERE NOT node IN $deletedNodes |
            [(node)--(neighbor) | neighbor]])])
    + [rel IN changedRelationships + $deletedRelationships | startNode(rel)]
    + [rel IN changedRelationships + $deletedRelationships | endNode(rel)],
    $deletedNodes) AS changedNodes
"""


def bind_changes(query: str) -> str:
    """Prefix a trigger query with the changedRelationships and changedNodes it reads."""
    changed_nodes = _CHANGED_NODES if "changedNodes" in query else ""
    return _CHANGED_RELATIONSHIPS + changed_nodes + query


@dataclass(frozen=True)
class Trigger:
    """A before-commit APOC trigger, and an audit that checks the same invariant against all existing data."""

    name: str
    description: str
    query: str
    audit: str


def _trigger(name: str, description: str, query: str, audit: str) -> Trigger:
    return Trigger(name, description, bind_changes(query), audit)


def _reject(name: str, message: str, node: str = "node", value: str | None = None) -> str:
    """Fail the transaction, listing every offending value, when any row reaches this point."""
    value = value or f"coalesce({node}.id, elementId({node}))"
    return f"""
        WITH collect(DISTINCT {value}) AS ids
        WHERE size(ids) > 0
        CALL apoc.util.validate(true, '{name}: {message}. IDs: %s', [string.join(ids, ', ')])
        RETURN null"""


def _audit(node: str, value: str | None = None) -> str:
    return f"RETURN DISTINCT {value or f'coalesce({node}.id, elementId({node}))'} AS violating_id"


def _affected_sources(
    source_label: str,
    rel_type: str,
    relationships: Literal["changedRelationships", "$deletedRelationships"] = "changedRelationships",
) -> str:
    """Select changed source nodes and the source endpoints of the relevant relationships."""
    return f"""CALL (*) {{
            UNWIND changedNodes AS n
            WITH n WHERE n:{source_label}
            RETURN n AS node
          UNION ALL
            UNWIND {relationships} AS rel
            WITH rel WHERE type(rel) = '{rel_type}'
            WITH startNode(rel) AS n
            WHERE n:{source_label}
            RETURN n AS node
        }}
        WITH DISTINCT node"""


def _multiple_outgoing(node: str, rel_type: str, target_label: str | None = None) -> str:
    """Keep nodes with more than one matching outgoing relationship."""
    target = f":{target_label}" if target_label else ""
    return f"""WITH {node}, count {{ ({node})-[:{rel_type}]->({target}) }} AS cnt
        WHERE cnt > 1"""


def _required_rel_trigger(name: str, source_label: str, rel_type: str, target_label: str) -> Trigger:
    missing = f"NOT (node)-[:{rel_type}]->(:{target_label})"
    return _trigger(
        name,
        f"{source_label} requires {rel_type}->{target_label}",
        f"""
        {_affected_sources(source_label, rel_type, "$deletedRelationships")}
        WHERE NOT (node IN $deletedNodes)
          AND {missing}
        {_reject(name, f"{source_label} must have {rel_type}->{target_label}")}
        """,
        f"MATCH (node:{source_label}) WHERE {missing} {_audit('node')}",
    )


def _max_one_trigger(name: str, source_label: str, rel_type: str, target_label: str | None = None) -> Trigger:
    """Limit a relationship to at most one target, of the given label when there is one."""
    target = f"->{target_label}" if target_label else ""
    return _trigger(
        name,
        f"{source_label} must have at most one {rel_type}{target}",
        f"""
        {_affected_sources(source_label, rel_type)}
        {_multiple_outgoing("node", rel_type, target_label)}
        {_reject(name, f"{source_label} must have at most one {rel_type}")}
        """,
        f"MATCH (node:{source_label}) {_multiple_outgoing('node', rel_type, target_label)} {_audit('node')}",
    )


def _rel_rule(name: str, description: str, rel_type: str, message: str, invalid: str) -> Trigger:
    """Reject relationships of the type whose `source` and `target` make `invalid` true."""
    return _trigger(
        name,
        description,
        f"""
        UNWIND changedRelationships AS rel
        WITH rel WHERE type(rel) = '{rel_type}'
        WITH startNode(rel) AS source, endNode(rel) AS target
        WHERE {invalid}
        {_reject(name, message, "source")}
        """,
        f"MATCH (source)-[:{rel_type}]->(target) WHERE {invalid} {_audit('source')}",
    )


def _rel_target_type_trigger(name: str, description: str, rel_type: str, target_label: str) -> Trigger:
    return _rel_rule(
        name, description, rel_type, f"{rel_type} must point to {target_label}", f"NOT target:{target_label}"
    )


def _no_self_ref_trigger(rel_type: str, description: str | None = None) -> Trigger:
    return _rel_rule(
        f"enforce_no_self_{rel_type.lower()}",
        description or f"{rel_type} must not point to self",
        rel_type,
        f"{rel_type} must not be self-referential",
        "source = target",
    )


# Deployed names that differ from the enforce_<source>_<relationship> convention.
_RELATIONSHIP_TRIGGER_NAMES = {
    ("Text", "TEXT_OF"): "enforce_text_has_text_of",
    ("BibliographicMetadata", "BIBLIOGRAPHY_OF"): "enforce_bibmeta_bibliography_of",
    ("BibliographicMetadata", "HAS_TYPE"): "enforce_bibmeta_has_type",
    ("TableOfContents", "TOC_OF"): "enforce_toc_edition",
    ("TableOfContentsSection", "SECTION_OF"): "enforce_toc_section",
    ("TableOfContentsSection", "HAS_TITLE"): "enforce_toc_section_title",
}


def _required_relationship_triggers() -> Iterator[Trigger]:
    for source, node in NODES.items():
        for rel_type, spec in node.get("relationships", {}).items():
            if not spec["required"]:
                continue
            name = _RELATIONSHIP_TRIGGER_NAMES.get((source, rel_type), f"enforce_{source.lower()}_{rel_type.lower()}")
            targets = [label.strip() for label in spec["target"].split("|")]
            yield _required_rel_trigger(name, source, rel_type, "|".join(targets))
            if spec["cardinality"] == "one":
                # For a union, count outgoing relationships across all target labels.
                yield _max_one_trigger(f"{name}_max_one", source, rel_type, targets[0] if len(targets) == 1 else None)


def _node_rule(name: str, description: str, label: str, message: str, invalid: str) -> Trigger:
    """Reject nodes of the label for which `invalid`, written over `node`, is true."""
    return _trigger(
        name,
        description,
        f"""
        UNWIND changedNodes AS node
        WITH node WHERE node:{label} AND ({invalid})
        {_reject(name, message)}
        """,
        f"MATCH (node:{label}) WHERE {invalid} {_audit('node')}",
    )


TRIGGERS: list[Trigger] = list(_required_relationship_triggers())

# Text — target type enforcement for optional typed rels
TRIGGERS.extend(
    _rel_target_type_trigger(
        f"enforce_{relationship.lower()}_target", f"{relationship} must point to a Text node", relationship, "Text"
    )
    for relationship in ("TRANSLATION_OF", "COMMENTARY_OF", "PART_OF")
)
TRIGGERS.append(_max_one_trigger("enforce_text_part_of_max_one", "Text", "PART_OF", "Text"))

TRIGGERS.append(
    _rel_target_type_trigger(
        "enforce_edition_has_segmentation_target",
        "HAS_SEGMENTATION must point to a Segmentation node",
        "HAS_SEGMENTATION",
        "Segmentation",
    )
)
TRIGGERS.append(
    _max_one_trigger("enforce_edition_has_segmentation_max_one", "Edition", "HAS_SEGMENTATION", "Segmentation")
)

# --- Diplomatic Edition must have exactly one Pagination ------------------
_DIPLOMATIC_PAGINATION_INVALID = """
    (node)-[:HAS_TYPE]->(:EditionType {name: 'diplomatic'})
    AND count { (node)<-[:PAGINATION_OF]-(:Pagination) } <> 1
"""
TRIGGERS.append(
    _trigger(
        "enforce_diplomatic_edition_has_pagination",
        "A diplomatic Edition must have exactly one Pagination",
        f"""
        CALL (*) {{
            UNWIND changedNodes AS n
            WITH n WHERE n:Edition
            RETURN n AS node
          UNION ALL
            UNWIND $deletedRelationships AS rel
            WITH rel WHERE type(rel) = 'PAGINATION_OF'
            WITH endNode(rel) AS n
            WHERE n:Edition
            RETURN n AS node
        }}
        WITH DISTINCT node
        WHERE NOT (node IN $deletedNodes) AND {_DIPLOMATIC_PAGINATION_INVALID}
        {_reject("enforce_diplomatic_edition_has_pagination", "must have exactly one Pagination")}
        """,
        f"MATCH (node:Edition) WHERE {_DIPLOMATIC_PAGINATION_INVALID} {_audit('node')}",
    )
)

# --- Excerpt editions: content comes only from spans of one stored edition -------
TRIGGERS.append(
    _node_rule(
        "enforce_excerpt_edition",
        "An excerpt edition has spans and EXCERPT_OF one stored edition, no content_key and no annotations of "
        "its own; other editions have neither EXCERPT_OF nor spans",
        "Edition",
        "Invalid excerpt edition content source",
        """
        CASE WHEN EXISTS { (node)-[:HAS_TYPE]->(:EditionType {name: 'excerpt'}) } THEN
            node.content_key IS NOT NULL
            OR count { (node)-[:EXCERPT_OF]->(:Edition) } <> 1
            OR NOT EXISTS { (:Span)-[:SPAN_OF]->(node) }
            OR EXISTS { (node)-[:EXCERPT_OF]->(source:Edition) WHERE source.content_key IS NULL }
            OR EXISTS { (node)-[:HAS_SEGMENTATION]->() }
            OR EXISTS { (node)<-[:PAGINATION_OF|TOC_OF|NOTE_OF|MARK_OF|BIBLIOGRAPHY_OF|ATTRIBUTE_OF]-() }
        ELSE EXISTS { (node)-[:EXCERPT_OF]->() } OR EXISTS { (:Span)-[:SPAN_OF]->(node) }
        END
        """,
    )
)
_SEGMENT_REFERENCE_DUPLICATE = _reject(
    "enforce_segment_reference_unique_per_segmentation", "Duplicate segment references", value="node.reference"
)
TRIGGERS.append(
    _trigger(
        "enforce_segment_reference_unique_per_segmentation",
        "Non-null Segment.reference values must be unique within a segmentation",
        f"""
        {_affected_sources("Segment", "SEGMENT_OF")}
        WHERE node.reference IS NOT NULL
        MATCH (node)-[:SEGMENT_OF]->(segmentation:Segmentation)<-[:SEGMENT_OF]-(other:Segment)
        WHERE other <> node AND other.reference = node.reference
        {_SEGMENT_REFERENCE_DUPLICATE}
        """,
        """
        MATCH (segmentation:Segmentation)<-[:SEGMENT_OF]-(segment:Segment)
        WHERE segment.reference IS NOT NULL
        WITH segmentation, segment.reference AS reference, count(segment) AS cnt
        WHERE cnt > 1
        RETURN segmentation.id + ':' + reference AS violating_id
        """,
    )
)


_AUDIO_ROLE_LIST = "[" + ", ".join(f"'{role}'" for role in AUDIO_ROLES) + "]"
_AUDIO_ROLE_INVALID = f"""EXISTS {{
    (node)-[:HAS_CONTRIBUTION]->(:Contribution)-[:WITH_ROLE]->(rt:RoleType)
    WHERE NOT coalesce(rt.name, '') IN {_AUDIO_ROLE_LIST}
}}"""

TRIGGERS.append(
    _trigger(
        "enforce_audio_contribution_role",
        "Every Contribution on an Audio must have a performer role (narrator, singer or musician)",
        f"""
        CALL (*) {{
            UNWIND changedNodes AS n
            WITH n WHERE n:Audio
            RETURN n AS node
          UNION ALL
            UNWIND changedNodes AS n
            WITH n WHERE n:Contribution
            MATCH (node:Audio)-[:HAS_CONTRIBUTION]->(n)
            RETURN node
          UNION ALL
            UNWIND changedNodes AS n
            WITH n WHERE n:RoleType AND NOT coalesce(n.name, '') IN {_AUDIO_ROLE_LIST}
            MATCH (node:Audio)-[:HAS_CONTRIBUTION]->(:Contribution)-[:WITH_ROLE]->(n)
            RETURN node
        }}
        WITH DISTINCT node
        WHERE NOT (node IN $deletedNodes) AND {_AUDIO_ROLE_INVALID}
        {_reject("enforce_audio_contribution_role", "Audio contributions must have role narrator, singer or musician")}
        """,
        f"MATCH (node:Audio) WHERE {_AUDIO_ROLE_INVALID} {_audit('node')}",
    )
)

# --- Audio: the recited Text belongs to the Audio's Work --------------------
_RECITATION_OUTSIDE_WORK = """(node)-[:RECITATION_OF]->(text:Text), (node)-[:AUDIO_OF]->(work:Work)
    WHERE NOT (text)-[:TEXT_OF]->(work)"""
TRIGGERS.append(
    _trigger(
        "enforce_audio_recitation_in_work",
        "The Text an Audio recites must have TEXT_OF to the Audio's AUDIO_OF Work",
        f"""
        CALL (*) {{
            UNWIND changedNodes AS n
            WITH n WHERE n:Audio
            RETURN n AS node
          UNION ALL
            UNWIND changedNodes AS n
            WITH n WHERE n:Text
            MATCH (node:Audio)-[:RECITATION_OF]->(n)
            RETURN node
          UNION ALL
            UNWIND changedNodes AS n
            WITH n WHERE n:Work
            MATCH (node:Audio)-[:AUDIO_OF]->(n)
            RETURN node
        }}
        WITH DISTINCT node
        WHERE NOT (node IN $deletedNodes)
        MATCH {_RECITATION_OUTSIDE_WORK}
        {_reject("enforce_audio_recitation_in_work", "Audio must recite a Text of its own Work")}
        """,
        f"MATCH {_RECITATION_OUTSIDE_WORK} {_audit('node')}",
    )
)

# --- Recording: at most one FOLLOWS, and only to an edition of the recited Text --
TRIGGERS.append(
    _rel_target_type_trigger("enforce_follows_target", "FOLLOWS must point to an Edition node", "FOLLOWS", "Edition")
)
for name, source, relationship, target in [
    ("enforce_recording_follows_max_one", "Recording", "FOLLOWS", "Edition"),
    ("enforce_audio_has_title_max_one", "Audio", "HAS_TITLE", "Nomen"),
    ("enforce_recording_has_title_max_one", "Recording", "HAS_TITLE", "Nomen"),
]:
    TRIGGERS.append(_max_one_trigger(name, source, relationship, target))

_FOLLOWS_OTHER_TEXT = """(node)-[:FOLLOWS]->(edition:Edition)
    WHERE NOT EXISTS { (edition)-[:EDITION_OF]->(:Text)<-[:RECITATION_OF]-(:Audio)<-[:RENDITION_OF]-(node) }"""
TRIGGERS.append(
    _trigger(
        "enforce_recording_follows_recited_text",
        "The Edition a Recording follows must be an edition of the Text its Audio recites",
        f"""
        CALL (*) {{
            UNWIND changedNodes AS n
            WITH n WHERE n:Recording
            RETURN n AS node
          UNION ALL
            UNWIND changedNodes AS n
            WITH n WHERE n:Edition
            MATCH (node:Recording)-[:FOLLOWS]->(n)
            RETURN node
          UNION ALL
            UNWIND changedNodes AS n
            WITH n WHERE n:Audio
            MATCH (node:Recording)-[:RENDITION_OF]->(n)
            RETURN node
        }}
        WITH DISTINCT node
        WHERE NOT (node IN $deletedNodes)
        MATCH {_FOLLOWS_OTHER_TEXT}
        {_reject("enforce_recording_follows_recited_text", "Recording must follow an edition of the recited Text")}
        """,
        f"MATCH {_FOLLOWS_OTHER_TEXT} {_audit('node')}",
    )
)

# --- Cue: only on a Recording that follows an Edition, and never across a FOLLOWS change --
TRIGGERS.append(
    _trigger(
        "enforce_cue_requires_follows",
        "Cues require FOLLOWS; removing or replacing FOLLOWS requires deleting the cues",
        f"""
        CALL (*) {{
            UNWIND changedNodes AS n
            WITH n WHERE n:Recording
            RETURN n AS node, false AS unfollowed
          UNION ALL
            UNWIND changedNodes AS n
            WITH n WHERE n:Cue
            MATCH (n)-[:CUE_OF]->(node:Recording)
            RETURN node, false AS unfollowed
          UNION ALL
            UNWIND $deletedRelationships AS rel
            WITH rel WHERE type(rel) = 'FOLLOWS'
            WITH startNode(rel) AS node
            WHERE node:Recording
            RETURN node, true AS unfollowed
        }}
        WITH node, any(flag IN collect(unfollowed) WHERE flag) AS unfollowed
        WHERE NOT (node IN $deletedNodes)
          AND EXISTS {{ (:Cue)-[:CUE_OF]->(node) }}
          AND (unfollowed OR NOT (node)-[:FOLLOWS]->(:Edition))
        {_reject("enforce_cue_requires_follows", "Cues need FOLLOWS and must be deleted when FOLLOWS changes")}
        """,
        f"""
        MATCH (node:Recording)
        WHERE EXISTS {{ (:Cue)-[:CUE_OF]->(node) }} AND NOT (node)-[:FOLLOWS]->(:Edition)
        {_audit("node")}
        """,
    )
)

TRIGGERS.append(
    _node_rule(
        "enforce_cue_start_ms_lt_end_ms",
        "Cue.start_ms must be less than Cue.end_ms",
        "Cue",
        "Cue.start_ms must be < Cue.end_ms",
        "node.start_ms >= node.end_ms",
    )
)


TRIGGERS.append(
    _node_rule(
        "enforce_text_translation_commentary_exclusive",
        "A Text must not have both TRANSLATION_OF and COMMENTARY_OF",
        "Text",
        "Text has both TRANSLATION_OF and COMMENTARY_OF",
        "(node)-[:TRANSLATION_OF]->() AND (node)-[:COMMENTARY_OF]->()",
    )
)

TRIGGERS.extend(
    _no_self_ref_trigger(relationship)
    for relationship in ("TRANSLATION_OF", "COMMENTARY_OF", "PART_OF", "ALTERNATIVE_OF", "HAS_PARENT", "ALIGNED_TO")
)

TRIGGERS.append(
    _node_rule(
        "enforce_span_start_lte_end",
        "Span.start must not be greater than Span.end",
        "Span",
        "Span.start must be <= Span.end",
        "node.start IS NULL OR node.end IS NULL OR node.start < 0 OR node.start > node.end",
    )
)

# Only one level of indirection: an alternative Nomen is never itself the target of ALTERNATIVE_OF.
TRIGGERS.append(
    _rel_rule(
        "enforce_nomen_no_alternative_chain",
        "A Nomen that is ALTERNATIVE_OF another must not itself be the target of ALTERNATIVE_OF",
        "ALTERNATIVE_OF",
        "ALTERNATIVE_OF must not form chains",
        "(source)<-[:ALTERNATIVE_OF]-() OR (target)-[:ALTERNATIVE_OF]->()",
    )
)

_ORIGINAL_TEXT_COUNT = "count { (work)<-[:TEXT_OF {original: true}]-(:Text) }"
TRIGGERS.append(
    _trigger(
        "enforce_work_one_original_text",
        "A Work can have at most one original Text (TEXT_OF with original:true)",
        f"""
        UNWIND changedRelationships AS rel
        WITH rel
        WHERE type(rel) = 'TEXT_OF' AND rel.original = true
        WITH endNode(rel) AS work
        WHERE work:Work AND {_ORIGINAL_TEXT_COUNT} > 1
        {_reject("enforce_work_one_original_text", "Work already has an original text", "work")}
        """,
        f"MATCH (work:Work) WHERE {_ORIGINAL_TEXT_COUNT} > 1 {_audit('work')}",
    )
)

TRIGGERS.append(
    _trigger(
        "enforce_text_title_unique",
        "Text title + language combination must be unique among texts written in that language",
        f"""
        UNWIND changedNodes AS candidate
        WITH DISTINCT candidate WHERE NOT candidate IN $deletedNodes
        MATCH (candidate)-[:HAS_TITLE|HAS_LOCALIZATION*0..2]->(node:LocalizedText)
        MATCH (node)-[:HAS_LANGUAGE]->(language:Language)
        WHERE EXISTS {{ (language)<-[:HAS_LANGUAGE]-(:Text)-[:HAS_TITLE]->(:Nomen)-[:HAS_LOCALIZATION]->(node) }}
        WITH DISTINCT node, language ORDER BY language.code
        WITH collect(DISTINCT node) AS titles, collect(DISTINCT language) AS languages
        // Serialize title checks on existing language nodes before reading competing titles.
        CALL apoc.lock.nodes(languages)
        UNWIND titles AS node
        MATCH (node)-[rel:HAS_LANGUAGE]->(lang:Language)
        WITH node, toLower(coalesce(rel.bcp47, lang.code)) AS title_lang
        MATCH (other:LocalizedText {{text: node.text}})-[other_rel:HAS_LANGUAGE]->(other_lang:Language)
        MATCH (other_lang)<-[:HAS_LANGUAGE]-(other_text:Text)-[:HAS_TITLE]->(:Nomen)-[:HAS_LOCALIZATION]->(other)
        WHERE toLower(coalesce(other_rel.bcp47, other_lang.code)) = title_lang
        WITH node.text AS text, title_lang AS lang, count(DISTINCT other_text) AS text_count
        WHERE text_count > 1
        {_reject("enforce_text_title_unique", "Duplicate title+language exists", value="text + ':' + lang")}
        """,
        """
        MATCH (l:Language)<-[:HAS_LANGUAGE]-(t:Text)-[:HAS_TITLE]->(:Nomen)-[:HAS_LOCALIZATION]->(lt:LocalizedText)
              -[rel:HAS_LANGUAGE]->(l)
        WITH lt.text AS text, toLower(coalesce(rel.bcp47, l.code)) AS lang, count(DISTINCT t) AS cnt
        WHERE cnt > 1
        RETURN text + ':' + lang AS violating_id
        """,
    )
)


TRIGGERS.append(_no_self_ref_trigger("SUBSECTION_OF", "Sections cannot parent themselves"))
TRIGGERS.append(
    _rel_target_type_trigger("enforce_has_parent_target", "HAS_PARENT targets Category", "HAS_PARENT", "Category")
)
TRIGGERS.append(
    _rel_target_type_trigger(
        "enforce_subsection_target", "SUBSECTION_OF targets a section", "SUBSECTION_OF", "TableOfContentsSection"
    )
)

_TOC_INVALID = """
    count { (s)-[:SUBSECTION_OF]->() } > 1
    OR EXISTS { (s)-[:SUBSECTION_OF*1..]->(s) }
    OR EXISTS {
        (s)-[:SUBSECTION_OF]->(p:TableOfContentsSection)
        WHERE NOT EXISTS { (s)-[:SECTION_OF]->(:TableOfContents)<-[:SECTION_OF]-(p) }
    }
    OR EXISTS {
        (child:Span)-[:SPAN_OF]->(s)-[:SUBSECTION_OF]->(:TableOfContentsSection)<-[:SPAN_OF]-(parent:Span)
        WHERE child.start < parent.start OR child.end > parent.end
    }
"""
TRIGGERS.append(
    _trigger(
        "enforce_toc_hierarchy",
        "TOC parents form a contained tree within one TOC",
        f"""
        UNWIND changedNodes AS n
        CALL (n) {{
            WITH n WHERE n:TableOfContentsSection RETURN n AS section
            UNION MATCH (n:Span)-[:SPAN_OF]->(section:TableOfContentsSection) RETURN section
        }}
        WITH section WHERE NOT section IN $deletedNodes
        UNWIND [section] + [(child:TableOfContentsSection)-[:SUBSECTION_OF]->(section) | child] AS s
        WITH DISTINCT s WHERE {_TOC_INVALID}
        {_reject("enforce_toc_hierarchy", "Invalid section hierarchy", "s")}
        """,
        f"MATCH (s:TableOfContentsSection) WHERE {_TOC_INVALID} {_audit('s')}",
    )
)


async def install_triggers(driver: AsyncDriver, database: str = DATABASE_NAME) -> None:
    """Upsert TRIGGERS by name, then drop stale ones (APOC commits each call separately, so never drop all first)."""

    async def write(tx: AsyncManagedTransaction) -> None:
        for trigger in TRIGGERS:
            await tx.run(
                "CALL apoc.trigger.install($database, $name, $statement, {phase: 'before'})",
                database=database,
                name=trigger.name,
                statement=f"CYPHER 25 {trigger.query}",
            )
            logger.info("Installed trigger: %s — %s", trigger.name, trigger.description)

        installed = await tx.run("CALL apoc.trigger.show($database) YIELD name", database=database)
        for name in {record["name"] async for record in installed} - {trigger.name for trigger in TRIGGERS}:
            await tx.run("CALL apoc.trigger.drop($database, $name)", database=database, name=name)

    async with driver.session(database=SYSTEM_DATABASE_NAME) as session:
        await session.execute_write(write)

    # Callers write right after installing; return only once the database runs the new rules.
    await wait_for_triggers(driver, database)
    logger.info("All %d triggers installed and live.", len(TRIGGERS))


async def wait_for_triggers(
    driver: AsyncDriver, database: str = DATABASE_NAME, wait_seconds: float = 120, interval: float = 1
) -> None:
    """Wait until the database runs TRIGGERS. APOC loads installed triggers only after its refresh interval.

    Live triggers must match the installed ones by name and statement, so a trigger whose statement changed
    under the same name also counts as pending.
    """
    names = {trigger.name for trigger in TRIGGERS}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_seconds
    while True:
        installed, _, _ = await driver.execute_query(
            "CALL apoc.trigger.show($database) YIELD name, query", database=database, database_=SYSTEM_DATABASE_NAME
        )
        running, _, _ = await driver.execute_query(
            "CALL apoc.trigger.list() YIELD name, query RETURN name, query", database_=database
        )
        expected = {record["name"]: record["query"] for record in installed}
        live = {record["name"]: record["query"] for record in running}
        if live == expected and set(expected) == names:
            return
        if loop.time() >= deadline:
            pending = names ^ set(expected) | {
                n for n in expected.keys() | live.keys() if expected.get(n) != live.get(n)
            }
            raise TimeoutError(f"Triggers not live after {wait_seconds}s; pending: {sorted(pending)[:5]}")
        await asyncio.sleep(interval)


async def audit_triggers(driver: AsyncDriver, database: str = DATABASE_NAME) -> dict[str, list[str]]:
    """Run all audit queries against the full database. Returns a dict of trigger name -> list of violating IDs.

    An empty dict means no violations found.
    """

    async def read(tx: AsyncManagedTransaction) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for trigger in TRIGGERS:
            result = await tx.run(cast(LiteralString, trigger.audit))
            violating_ids = [str(value) for value in await result.value("violating_id")]
            if violating_ids:
                found[trigger.name] = violating_ids
                preview = ", ".join(violating_ids[:5])
                suffix = "…" if len(violating_ids) > 5 else ""
                logger.warning(
                    "Audit violation [%s]: %d nodes — %s%s",
                    trigger.name,
                    len(violating_ids),
                    preview,
                    suffix,
                )
        return found

    async with driver.session(database=database) as session:
        violations = await session.execute_read(read)

    if not violations:
        logger.info("Audit passed: no violations found across %d constraints.", len(TRIGGERS))
    else:
        logger.warning("Audit found violations in %d constraint(s).", len(violations))

    return violations
