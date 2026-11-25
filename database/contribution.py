"""Create and delete Contribution subgraphs hanging off any node that has HAS_CONTRIBUTION.

Source labels are trusted LiteralString constants, keeping unique-index seeks explicit.
Neo4j 2025.11+ supports property indexes with dynamic labels, subject to planner limitations:
https://neo4j.com/docs/cypher-manual/25/clauses/match/#dynamic-match
The reviewed 2026.07.1 plan still used DynamicLabelNodeLookup for the dynamic equivalent.
Retain static labels unless PROFILE on the deployed version shows comparable DB hits.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING, LiteralString

from database import nomen
from errors import DataNotFoundError, DataValidationError
from models.contribution import ContributionInputItem, PersonContributionBase, PersonContributionInput

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction


def return_list(source: LiteralString) -> LiteralString:
    """Project person and AI contributions for the source node in a Cypher return map."""
    path = f"({source})-[:HAS_CONTRIBUTION]->(cn_c:Contribution)-[:BY]->"
    role = "[(cn_c)-[:WITH_ROLE]->(cn_role:RoleType) | cn_role.name][0]"
    name = "(cn_person)-[:HAS_NAME]->(cn_nomen:Nomen WHERE NOT (cn_nomen)-[:ALTERNATIVE_OF]->(:Nomen))"
    return f"""(
        [{path}(cn_person:Person) | {{
            type: "person", id: cn_person.id, bdrc_id: cn_person.bdrc, role: {role},
            name: {nomen.optional_localized_map(name)}
        }}]
        + [{path}(cn_ai:AI) | {{type: "ai", id: cn_ai.id, role: {role}}}]
    )"""


def create_person_query(label: LiteralString) -> LiteralString:
    return f"""
    MATCH (source:{label} {{id: $source_id}})
    MATCH (p:Person) WHERE (($person_id IS NOT NULL AND p.id = $person_id)
                            OR ($person_bdrc_id IS NOT NULL AND p.bdrc = $person_bdrc_id))
    MERGE (rt:RoleType {{name: $role_name}})
    CREATE (source)-[:HAS_CONTRIBUTION]->(c:Contribution)-[:BY]->(p),
           (c)-[:WITH_ROLE]->(rt)
    RETURN elementId(c) AS contribution_element_id
    """


def create_ai_query(label: LiteralString) -> LiteralString:
    return f"""
    MATCH (source:{label} {{id: $source_id}})
    MERGE (rt:RoleType {{name: $role_name}})
    MERGE (ai:AI {{id: $ai_id}})
    CREATE (source)-[:HAS_CONTRIBUTION]->(c:Contribution)-[:BY]->(ai),
        (c)-[:WITH_ROLE]->(rt)
    RETURN elementId(c) AS contribution_element_id
    """


async def create_all(
    tx: AsyncManagedTransaction,
    source_label: LiteralString,
    source_id: str,
    contributions: Sequence[ContributionInputItem],
) -> None:
    for contribution in contributions:
        if isinstance(contribution, PersonContributionBase):
            query = create_person_query(source_label)
            params = {"person_id": contribution.id, "person_bdrc_id": contribution.bdrc_id}
            not_found = (
                f"Person or Role not found. Person: id={contribution.id}, bdrc_id={contribution.bdrc_id}; "
                f"Role: {contribution.role.value}"
            )
        else:
            query = create_ai_query(source_label)
            params = {"ai_id": contribution.id}
            not_found = f"AI contribution creation failed. AI: {contribution.id}; Role: {contribution.role.value}"
        result = await tx.run(query, params, source_id=source_id, role_name=contribution.role.value)
        if not await result.single():
            raise DataNotFoundError(not_found)


async def replace(
    tx: AsyncManagedTransaction,
    source_label: LiteralString,
    source_id: str,
    contributions: Sequence[ContributionInputItem],
) -> None:
    await validate_contribution_references(tx, contributions)
    await tx.run(
        f"""
        MATCH (:{source_label} {{id: $source_id}})-[:HAS_CONTRIBUTION]->(c:Contribution)
        DETACH DELETE c
        FINISH
        """,
        source_id=source_id,
    )
    await create_all(tx, source_label, source_id, contributions)


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
