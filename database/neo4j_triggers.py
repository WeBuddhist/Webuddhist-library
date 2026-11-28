import asyncio
import json
import logging
import math
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, LiteralString, cast

from neo4j import AsyncDriver, AsyncManagedTransaction
from pydantic.alias_generators import to_snake

from database.conditions import CHANGED_RELATIONSHIPS, ENDPOINT_RELATIONSHIPS, ConditionCompiler
from database.schema import NODES, RELATIONSHIPS, ConditionalRule, Relationship, property_flag

logger = logging.getLogger(__name__)

DATABASE_NAME = "neo4j"
SYSTEM_DATABASE_NAME = "system"

# Manual graph checks may also depend on a changed node's neighbors.
_CHANGED_NODES = f"""
WITH {CHANGED_RELATIONSHIPS} AS changedRelationships
WITH apoc.coll.subtract($createdNodes
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


@dataclass(frozen=True)
class Check:
    name: str
    message: str
    invalid: str


@dataclass(frozen=True)
class Trigger:
    """A before-commit query and full-graph audit compiled from the same checks."""

    name: str
    rules: dict[str, str]
    query: str
    audit_matches: str

    def audit_query(self, exclude: str | None = None) -> str:
        condition = f" AND NOT ({exclude})" if exclude else ""
        return f"{self.audit_matches}{condition} RETURN DISTINCT violating_id, failure.rule AS rule"

    @property
    def audit(self) -> str:
        return self.audit_query()


def _trigger(
    candidates: str,
    scan: str,
    *checks: Check,
    name: str | None = None,
    id_expression: str = "coalesce(toStringOrNull(node.id), elementId(node))",
) -> Trigger:
    cases = ",\n".join(
        f"CASE WHEN {c.invalid} THEN {{rule: {json.dumps(c.name)}, message: {json.dumps(c.message)}}} END"
        for c in checks
    )
    violations = f"""
        UNWIND [{cases}] AS failure
        WITH *, {id_expression} AS violating_id WHERE failure IS NOT NULL"""
    return Trigger(
        name or checks[0].name,
        {c.name: c.message for c in checks},
        f"""{candidates} {violations}
            WITH failure, collect(DISTINCT violating_id) AS ids
            WITH collect(failure.rule + ': ' + failure.message + ' IDs: ' + string.join(ids, ', ')) AS errors
            WHERE size(errors) > 0
            CALL apoc.util.validate(true, '%s', [string.join(errors, '; ')])
            RETURN null""",
        f"{scan} {violations}",
    )


def _rel_rule(name: str, message: str, rel: str, invalid: str) -> Trigger:
    return _trigger(
        f"""UNWIND {ENDPOINT_RELATIONSHIPS} AS rel WITH rel WHERE type(rel) = '{rel}'
            WITH rel, startNode(rel) AS source, endNode(rel) AS target""",
        f"MATCH (source)-[rel:{rel}]->(target)",
        Check(name, message, invalid),
        id_expression="coalesce(toStringOrNull(source.id), elementId(source))",
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
_ENDPOINT_TRIGGER_NAMES = {
    "HAS_SEGMENTATION": "enforce_edition_has_segmentation_target",
    "SUBSECTION_OF": "enforce_subsection_target",
}


_MAX_DEGREE = {"0": 0, "0..1": 1, "1": 1}


def _relationship_degrees(
    name: str,
    rel: Relationship,
    compiler: ConditionCompiler,
    rule: ConditionalRule | None,
    index: int,
) -> Iterator[Trigger]:
    targets = rule.target if rule else list(rel.per_target)
    # The source counts all allowed target labels together. Each target label has its own incoming bound.
    descriptions = set(rel.descriptions.values())
    shared = descriptions.pop() if len(descriptions) == 1 else None
    ends = [(name, rel.source, targets, rel.per_source, False, shared)]
    ends.extend(
        (f"{name}_per_{to_snake(t)}", t, [rel.source], rel.per_target[t], True, rel.descriptions.get(t))
        for t in targets
    )
    for stem, label, peers, base, reverse, base_description in ends:
        bound = rule.cardinality[0 if reverse else 1] if rule else base
        description = rule.description if rule else base_description
        identifier = f"{stem}_when_{index}" if rule else stem
        affected, pattern, guards = compiler.degree(rel, rule, label, peers, reverse=reverse)
        link = f"matching {rel.type}" if rule else rel.type
        other = "|".join(peers)
        checks = []
        if bound in ("1", "1..*"):
            message = f"{label} must have {link} from {other}" if reverse else f"{label} must have {link}->{other}"
            checks.append(Check(identifier, description or message, f"NOT EXISTS {{ {pattern} }}"))
        maximum = _MAX_DEGREE.get(bound, math.inf)
        if maximum < (_MAX_DEGREE.get(base, math.inf) if rule else math.inf):
            # Unfiltered outgoing maxima include edges with invalid targets.
            counted = pattern if rule or reverse else f"(node)-[:{rel.type}]->()"
            limit = "zero" if maximum == 0 else "one"
            message = f"{label} must have at most {limit} {link}" + (f" from {other}" if reverse else "")
            checks.append(
                Check(f"{identifier}_max_{limit}", description or message, f"count {{ {counted} }} > {maximum}")
            )
        if checks:
            guard = " WHERE " + " AND ".join(guards) if guards else ""
            yield _trigger(affected + guard, f"MATCH (node:{label}){guard}", *checks)


def _relationship_triggers(
    definitions: tuple[Relationship, ...] = RELATIONSHIPS,
    *,
    nodes: dict[str, dict[str, Any]] = NODES,
) -> Iterator[Trigger]:
    compiler = ConditionCompiler(definitions, nodes)
    pairs: dict[str, list[str]] = defaultdict(list)
    no_self_sources: dict[str, list[str]] = defaultdict(list)
    for definition in definitions:
        source, rel_type = definition.source, definition.type
        target = "|".join(definition.per_target)
        pairs[rel_type].append(f"(source:{source} AND target:{target})")
        if not definition.allow_self:
            no_self_sources[rel_type].append(source)
        name = _RELATIONSHIP_TRIGGER_NAMES.get((source, rel_type), f"enforce_{source.lower()}_{rel_type.lower()}")
        for index, rule in enumerate((None, *definition.conditions)):
            yield from _relationship_degrees(name, definition, compiler, rule, index)
        for prop, field in definition.properties.items():
            invalid = _invalid_property(f"rel.{prop}", field)
            if invalid:
                message = field.get("description") or f"{source}.{rel_type}.{prop} violates its property definition"
                yield _rel_rule(f"{name}_{prop}_property", message, rel_type, f"source:{source} AND ({invalid})")
    for rel_type, allowed_pairs in pairs.items():
        name = _ENDPOINT_TRIGGER_NAMES.get(rel_type, f"enforce_{rel_type.lower()}_target")
        message = "These record types cannot be linked by this relationship in this direction."
        yield _rel_rule(name, message, rel_type, "NOT (" + " OR ".join(allowed_pairs) + ")")
        if sources := no_self_sources.get(rel_type):
            invalid = "source = target"
            if len(sources) != len(allowed_pairs):
                invalid += " AND source:" + "|".join(sources)
            message = "A record cannot link to itself through this relationship."
            yield _rel_rule(f"enforce_no_self_{rel_type.lower()}", message, rel_type, invalid)


_CYPHER_TYPES = {"string": "STRING", "integer": "INTEGER", "boolean": "BOOLEAN", "datetime": "ZONED DATETIME"}


def _invalid_property(field: str, spec: dict[str, Any], *extra_options: str) -> str:
    if unknown := spec.keys() - {"type", "required", "description", *extra_options}:
        raise ValueError(f"Unknown property options for {field}: {sorted(unknown)}")
    if (description := spec.get("description")) is not None and not isinstance(description, str):
        raise TypeError(f"{field}: description must be a string")
    required = property_flag(field, spec, "required")
    if spec["type"] == "any":
        return f"{field} IS NULL" if required else ""
    # A nullable Cypher type admits missing optional properties.
    nullable = " NOT NULL" if required else ""
    return f"NOT ({field} IS :: {_CYPHER_TYPES[spec['type']]}{nullable})"


def _property_triggers() -> Iterator[Trigger]:
    for label, node in NODES.items():
        name = f"enforce_{to_snake(label)}"
        checks = {}
        for prop, spec in node.get("properties", {}).items():
            invalid = _invalid_property(f"node.{prop}", spec, "unique", "indexed")
            if invalid:
                message = spec.get("description") or f"Invalid {label}.{prop}"
                checks[prop] = Check(f"{name}_{prop}_property", message, invalid)
        if checks:
            # Only creation, gaining the label, or an edit to a checked property can invalidate it.
            candidates = f"""
                UNWIND apoc.coll.subtract($createdNodes + coalesce($assignedLabels[{json.dumps(label)}], [])
                    + coll.flatten([key IN {json.dumps(list(checks))} | [entry IN
                        coalesce($assignedNodeProperties[key], []) + coalesce($removedNodeProperties[key], [])
                        | entry.node]]), $deletedNodes) AS node
                WITH node WHERE node:{label}"""
            yield _trigger(candidates, f"MATCH (node:{label})", *checks.values(), name=f"{name}_properties")


_TOC_INVALID = """
    EXISTS { (s)-[:SUBSECTION_OF*1..]->(s) }
    OR EXISTS {
        (s)-[:SUBSECTION_OF]->(p:TableOfContentsSection)
        WHERE NOT EXISTS { (s)-[:SECTION_OF]->(:TableOfContents)<-[:SECTION_OF]-(p) }
    }
    OR EXISTS {
        (child:Span)-[:SPAN_OF]->(s)-[:SUBSECTION_OF]->(:TableOfContentsSection)<-[:SPAN_OF]-(parent:Span)
        WHERE child.start < parent.start OR child.end > parent.end
    }
"""

TRIGGERS = [
    *_relationship_triggers(),
    *_property_triggers(),
    _trigger(
        f"""
        {_CHANGED_NODES}
        UNWIND changedNodes AS n
        MATCH (node:Recording)-[:FOLLOWS|RENDITION_OF*0..1]->(n)
        WITH DISTINCT node
        MATCH (node)-[:FOLLOWS]->(edition:Edition)
        """,
        "MATCH (node:Recording)-[:FOLLOWS]->(edition:Edition)",
        Check(
            "enforce_recording_follows_recited_text",
            "The edition a recording follows must belong to the text recited in its audio performance.",
            "NOT EXISTS { (edition)-[:EDITION_OF]->(:Text)<-[:RECITATION_OF]-(:Audio)<-[:RENDITION_OF]-(node) }",
        ),
    ),
    _trigger(
        f"""
        {_CHANGED_NODES}
        CALL (changedNodes) {{
            UNWIND changedNodes AS candidate
            MATCH (candidate)-[:HAS_TITLE|HAS_LOCALIZATION*0..2]->(node:LocalizedText)
            RETURN node
          UNION ALL
            UNWIND coalesce($assignedNodeProperties.code, []) AS change
            WITH change.node AS lang WHERE NOT lang IN $createdNodes + $deletedNodes
            MATCH (node:LocalizedText)-[tag:HAS_LANGUAGE]->(lang:Language)
            WHERE tag.bcp47 IS NULL
            RETURN node
        }}
        MATCH (node)-[:HAS_LANGUAGE]->(language:Language)
        WHERE node.text IS :: STRING NOT NULL
          AND EXISTS {{ (language)<-[:HAS_LANGUAGE]-(:Text)-[:HAS_TITLE]->(:Nomen)-[:HAS_LOCALIZATION]->(node) }}
        WITH DISTINCT node, language ORDER BY language.code
        WITH collect(DISTINCT node) AS titles, collect(DISTINCT language) AS languages
        // Serialize title checks on existing language nodes before reading competing titles.
        CALL apoc.lock.nodes(languages)
        UNWIND titles AS node
        MATCH (node)-[rel:HAS_LANGUAGE]->(lang:Language)
        WITH node, toLower(toStringOrNull(coalesce(rel.bcp47, lang.code))) AS title_lang
        MATCH (other:LocalizedText {{text: node.text}})-[other_rel:HAS_LANGUAGE]->(other_lang:Language)
        MATCH (other_lang)<-[:HAS_LANGUAGE]-(other_text:Text)-[:HAS_TITLE]->(:Nomen)-[:HAS_LOCALIZATION]->(other)
        WHERE toLower(toStringOrNull(coalesce(other_rel.bcp47, other_lang.code))) = title_lang
        WITH node.text AS text, title_lang AS lang, count(DISTINCT other_text) AS text_count
        """,
        """
        MATCH (l:Language)<-[:HAS_LANGUAGE]-(t:Text)-[:HAS_TITLE]->(:Nomen)-[:HAS_LOCALIZATION]->(lt:LocalizedText)
              -[rel:HAS_LANGUAGE]->(l)
        WHERE lt.text IS :: STRING NOT NULL
        WITH lt.text AS text, toLower(toStringOrNull(coalesce(rel.bcp47, l.code))) AS lang,
             count(DISTINCT t) AS text_count
        WHERE lang IS NOT NULL
        """,
        Check(
            "enforce_text_title_unique",
            "Another text already uses this title in the same language.",
            "text_count > 1",
        ),
        id_expression="text + ':' + lang",
    ),
    _trigger(
        f"""
        {_CHANGED_NODES}
        UNWIND changedNodes AS n
        MATCH (n)-[:SPAN_OF*0..1]->(section:TableOfContentsSection)
        UNWIND [section] + [(child:TableOfContentsSection)-[:SUBSECTION_OF]->(section) | child] AS s
        WITH DISTINCT s
        """,
        "MATCH (s:TableOfContentsSection)",
        Check(
            "enforce_toc_hierarchy",
            "A section and its parent must belong to the same table of contents. The child\u2019s span must fit "
            "inside the parent\u2019s span, and following parent sections must never lead back to the same "
            "section.",
            _TOC_INVALID,
        ),
        id_expression="coalesce(toStringOrNull(s.id), elementId(s))",
    ),
]

RULE_DESCRIPTIONS = {name: description for trigger in TRIGGERS for name, description in trigger.rules.items()}


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
            logger.info("Installed trigger: %s", trigger.name)

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


async def audit_triggers(
    driver: AsyncDriver,
    database: str = DATABASE_NAME,
    *,
    exclusions: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Return violating IDs by rule across the full database."""

    async def read(tx: AsyncManagedTransaction) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for trigger in TRIGGERS:
            query = trigger.audit_query(exclusions.get(trigger.name) if exclusions else None)
            result = await tx.run(cast(LiteralString, query))
            async for record in result:
                found.setdefault(record["rule"], []).append(str(record["violating_id"]))
        return found

    async with driver.session(database=database) as session:
        return await session.execute_read(read)
