from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, LiteralString

from database import nomen
from database.content_state import read_value, touch_annotation, touch_edition, validate_edition_exists
from database.excerpt import read_edition
from database.repository import SessionSource, fetch_record, reading, writing
from errors import DataNotFoundError
from identifier import generate_id
from models.annotation import (
    TableOfContentsInput,
    TableOfContentsOutput,
    TableOfContentsSectionInput,
    TableOfContentsSectionOutput,
)

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction, Record

    from database.excerpt import ExcerptMap


CREATE_TOC_QUERY: LiteralString = """
MATCH (edition:Edition {id: $edition_id})
CREATE (toc:TableOfContents {id: $toc_id})-[:TOC_OF]->(edition)
WITH toc
CALL (*) {
    WHEN $metadata_id IS NOT NULL THEN {
        CREATE (metadata:AnnotationMetadata {id: $metadata_id, name: $metadata_name})
        CREATE (toc)-[:HAS_METADATA]->(metadata)
    }
}
RETURN toc.id AS id
"""
CREATE_SECTIONS_QUERY: LiteralString = """
MATCH (toc:TableOfContents {id: $toc_id})
UNWIND $sections AS section_data
MATCH (title:Nomen {id: section_data.title_nomen_id})
OPTIONAL MATCH (summary:Nomen {id: section_data.summary_nomen_id})
CREATE (section:TableOfContentsSection {id: section_data.id})-[:SECTION_OF]->(toc)
CREATE (section)-[:HAS_TITLE]->(title)
CREATE (:Span {start: section_data.span_start, end: section_data.span_end})-[:SPAN_OF]->(section)
WITH section, summary
CALL (*) {
    WHEN summary IS NOT NULL THEN {
        CREATE (section)-[:HAS_SUMMARY]->(summary)
    }
}
RETURN count(section) AS count
"""
CREATE_HIERARCHY_QUERY: LiteralString = """
UNWIND $sections AS section_data
WITH section_data
WHERE section_data.parent_id IS NOT NULL
MATCH (section:TableOfContentsSection {id: section_data.id})
MATCH (parent:TableOfContentsSection {id: section_data.parent_id})
CREATE (section)-[:SUBSECTION_OF]->(parent)
RETURN count(section) AS count
"""
GET_BY_ID_QUERY: LiteralString = """
MATCH (toc:TableOfContents {id: $toc_id})-[:TOC_OF]->(edition:Edition)-[:EDITION_OF]->(text:Text)
OPTIONAL MATCH (toc)-[:HAS_METADATA]->(metadata:AnnotationMetadata)
RETURN {
    id: toc.id,
    edition_id: edition.id,
    text_id: text.id,
    metadata: CASE WHEN metadata IS NULL THEN null ELSE {name: metadata.name} END
} AS toc
"""
GET_BY_EDITION_ID_QUERY: LiteralString = """
MATCH (edition:Edition {id: $edition_id})-[:EDITION_OF]->(text:Text)
MATCH (toc:TableOfContents)-[:TOC_OF]->(edition)
OPTIONAL MATCH (toc)-[:HAS_METADATA]->(metadata:AnnotationMetadata)
RETURN {
    id: toc.id,
    edition_id: edition.id,
    text_id: text.id,
    metadata: CASE WHEN metadata IS NULL THEN null ELSE {name: metadata.name} END
} AS toc
ORDER BY toc.id
"""
GET_SECTIONS_QUERY: LiteralString = f"""
MATCH (section:TableOfContentsSection)-[:SECTION_OF]->(:TableOfContents {{id: $toc_id}})
MATCH (span:Span)-[:SPAN_OF]->(section)
OPTIONAL MATCH (section)-[:SUBSECTION_OF]->(parent:TableOfContentsSection)
RETURN {{
    id: section.id,
    title: {nomen.localized_map("(section)-[:HAS_TITLE]->(:Nomen)")},
    summary: nullIf({nomen.localized_map("(section)-[:HAS_SUMMARY]->(:Nomen)")}, {{}}),
    span: {{start: span.start, end: span.end}}
}} AS section,
parent.id AS parent_id
ORDER BY parent_id, section.span.start, section.span.end, section.id
"""
_DELETE_BODY: LiteralString = """
OPTIONAL MATCH (toc)-[:HAS_METADATA]->(metadata:AnnotationMetadata)
OPTIONAL MATCH (section:TableOfContentsSection)-[:SECTION_OF]->(toc)
OPTIONAL MATCH (span:Span)-[:SPAN_OF]->(section)
OPTIONAL MATCH (section)-[:HAS_TITLE|HAS_SUMMARY]->(nomen:Nomen)
OPTIONAL MATCH (nomen)-[:HAS_LOCALIZATION]->(localized:LocalizedText)
DETACH DELETE span, localized, nomen, section, metadata, toc
FINISH
"""
DELETE_QUERY: LiteralString = f"""
MATCH (toc:TableOfContents {{id: $toc_id}})
{_DELETE_BODY}
"""
DELETE_ALL_QUERY: LiteralString = f"""
MATCH (toc:TableOfContents)-[:TOC_OF]->(:Edition {{id: $edition_id}})
{_DELETE_BODY}
"""


async def _flatten_sections(
    tx: AsyncManagedTransaction, sections: list[TableOfContentsSectionInput], *, parent_id: str | None = None
) -> list[dict[str, str | int | None]]:
    flattened: list[dict[str, str | int | None]] = []
    nomens: dict[str, dict[str, str]] = {}

    def visit(items: list[TableOfContentsSectionInput], parent: str | None) -> None:
        for section in items:
            section_id, title_id = (generate_id(), generate_id())
            nomens[title_id] = section.title.root
            summary_id = generate_id() if section.summary is not None else None
            if summary_id is not None and section.summary is not None:
                nomens[summary_id] = section.summary.root
            flattened.append(
                {
                    "id": section_id,
                    "parent_id": parent,
                    "title_nomen_id": title_id,
                    "summary_nomen_id": summary_id,
                    "span_start": section.span.start,
                    "span_end": section.span.end,
                }
            )
            visit(section.subsections, section_id)

    visit(sections, parent_id)
    await nomen.create_many(tx, nomens)
    return flattened


def _build_sections(records: Sequence[dict[str, Any] | Record]) -> list[TableOfContentsSectionOutput]:
    nodes: dict[str, TableOfContentsSectionOutput] = {}
    parent_ids: dict[str, str | None] = {}
    for record in records:
        node = TableOfContentsSectionOutput.model_validate(record["section"])
        nodes[node.id] = node
        parent_ids[node.id] = record["parent_id"]
    roots: list[TableOfContentsSectionOutput] = []
    for node_id, node in nodes.items():
        parent_id = parent_ids[node_id]
        if parent_id and parent_id in nodes:
            nodes[parent_id].subsections.append(node)
        else:
            roots.append(node)
    for node in nodes.values():
        node.subsections.sort(key=lambda item: (item.span.start, item.span.end, item.id))
    roots.sort(key=lambda item: (item.span.start, item.span.end, item.id))
    return roots


async def _build_toc_output(tx: AsyncManagedTransaction, record: dict[str, Any] | Record) -> TableOfContentsOutput:
    toc_data = dict(record["toc"])
    result = await tx.run(GET_SECTIONS_QUERY, toc_id=toc_data["id"])
    sections = _build_sections(await result.data())
    return TableOfContentsOutput.model_validate(toc_data | {"sections": sections})


async def _get_all(tx: AsyncManagedTransaction, edition_id: str) -> list[TableOfContentsOutput]:
    await validate_edition_exists(tx, edition_id)
    result = await tx.run(GET_BY_EDITION_ID_QUERY, edition_id=edition_id)
    return [await _build_toc_output(tx, record) for record in await result.data()]


@writing
async def add(tx: AsyncManagedTransaction, edition_id: str, toc: TableOfContentsInput) -> str:
    state = await touch_edition(tx, edition_id)
    state.validate_span(toc.max_end)
    toc_id = generate_id()
    metadata_id = generate_id() if toc.metadata is not None else None
    metadata_name = toc.metadata.name if toc.metadata is not None else None
    sections = await _flatten_sections(tx, toc.sections)
    result = await tx.run(
        CREATE_TOC_QUERY, edition_id=edition_id, toc_id=toc_id, metadata_id=metadata_id, metadata_name=metadata_name
    )
    record = await result.single(strict=True)
    writes: tuple[tuple[LiteralString, int], ...] = (
        (CREATE_SECTIONS_QUERY, len(sections)),
        (CREATE_HIERARCHY_QUERY, sum(s["parent_id"] is not None for s in sections)),
    )
    for query, expected in writes:
        result = await tx.run(query, toc_id=toc_id, sections=sections)
        if (await result.single(strict=True))["count"] != expected:
            raise DataNotFoundError(f"Failed to create table of contents sections for '{toc_id}'")
    return str(record["id"])


@writing
async def delete(tx: AsyncManagedTransaction, toc_id: str) -> None:
    await touch_annotation(tx, "TableOfContents", toc_id)
    await tx.run(DELETE_QUERY, toc_id=toc_id)


@reading
async def get(tx: AsyncManagedTransaction, toc_id: str) -> TableOfContentsOutput:
    record = await fetch_record(tx, GET_BY_ID_QUERY, f"Table of contents with ID '{toc_id}' not found", toc_id=toc_id)

    return await read_value(tx, record["toc"]["edition_id"], lambda tx: _build_toc_output(tx, record))


async def get_all(db: SessionSource, edition_id: str) -> list[TableOfContentsOutput]:

    async def read(
        tx: AsyncManagedTransaction, source_id: str, excerpt: ExcerptMap | None
    ) -> list[TableOfContentsOutput]:
        tocs = await _get_all(tx, source_id)
        return excerpt.project_tocs(tocs) if excerpt else tocs

    return await db.read(read_edition, edition_id, read)
