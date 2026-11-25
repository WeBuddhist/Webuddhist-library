from typing import TYPE_CHECKING, Any, Literal, LiteralString

from pydantic import MISSING

from errors import DataConflictError, DataValidationError
from identifier import generate_id
from models.base import base_language_code
from models.requests import TextFacetFilter, TextFilter
from models.responses import TextLanguageResponse
from models.text import TextBase, TextInput, TextOutput, TextPatch

from . import category, contribution, nomen, tag
from . import language as language_database
from .cypher import application_tag_ids, has_language, match_in_id_order, replace_language, replace_license
from .repository import (
    SessionSource,
    fetch_all,
    fetch_one,
    fetch_record,
    on_constraint_violation,
    query_all,
    query_record,
    raise_if_blockers,
    reading,
    set_properties,
    writing,
)

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction, Record

TEXT_LABEL: LiteralString = "Text"
_TEXT_RETURN: LiteralString = f"""
{{
    id: e.id,
    bdrc: e.bdrc,
    wiki: e.wiki,
    commentary_of: [(e)-[:COMMENTARY_OF]->(c_target:Text) | c_target.id][0],
    translation_of: [(e)-[:TRANSLATION_OF]->(t_target:Text) | t_target.id][0],
    part_of: [(e)-[:PART_OF]->(whole:Text) | whole.id][0],
    commentaries: [(e)<-[:COMMENTARY_OF]-(c_child:Text) | c_child.id],
    translations: [(e)<-[:TRANSLATION_OF]-(t_child:Text) | t_child.id],
    parts: [(e)<-[:PART_OF]-(part:Text) | part.id],
    contributions: {contribution.return_list("e")},
    date: e.date,
    title: {nomen.localized_map("(e)-[:HAS_TITLE]->(n:Nomen WHERE NOT (n)-[:ALTERNATIVE_OF]->(:Nomen))")},
    alt_titles: {nomen.alternative_maps("(e)-[:HAS_TITLE]->(:Nomen)")},
    language: [(e)-[r:HAS_LANGUAGE]->(lang:Language) | coalesce(r.bcp47, lang.code)][0],
    category_id: [(e)-[:TEXT_OF]->(work:Work)-[:HAS_CATEGORY]->(cat:Category) | cat.id][0],
    license: coalesce([(e)-[:HAS_LICENSE]->(license:LicenseType) | license.name][0], "public"),
    editions: [(e)<-[:EDITION_OF]-(m:Edition) | m.id],
    audios: [(e)<-[:RECITATION_OF]-(a:Audio) | a.id],
    tag_ids: {application_tag_ids("(e)-[:TEXT_OF]->(:Work)")}
}} AS text
"""
GET_QUERY: LiteralString = f"""
MATCH (e:Text {{id: $id}})-[:TEXT_OF]->(w:Work)
RETURN {_TEXT_RETURN}, w.id AS work_id
"""
GET_BY_IDS_QUERY: LiteralString = f"""
{match_in_id_order("e", "Text")}
RETURN {_TEXT_RETURN}
"""
_FILTERED_TEXTS: LiteralString = f"""
CALL () {{
    WHEN $bdrc IS NOT NULL THEN {{ MATCH (e:Text {{bdrc: $bdrc}}) RETURN e }}
    WHEN $wiki IS NOT NULL THEN {{ MATCH (e:Text {{wiki: $wiki}}) RETURN e }}
    WHEN $author_id IS NOT NULL THEN {{
        MATCH (e:Text)-[:HAS_CONTRIBUTION]->(:Contribution)-[:BY]->(:Person {{id: $author_id}})
        RETURN DISTINCT e
    }}
    WHEN $category_id IS NOT NULL THEN {{
        MATCH (e:Text)-[:TEXT_OF]->(:Work)-[:HAS_CATEGORY]->(:Category {{id: $category_id}}) RETURN e
    }}
    WHEN size($tag_groups) > 0 THEN {{
        MATCH (e:Text)-[:TEXT_OF]->(:Work)-[:HAS_TAG]->(t:Tag) WHERE t.id IN $tag_groups[0] RETURN DISTINCT e
    }}
    WHEN $language IS NOT NULL THEN {{
        MATCH (e:Text)-[:HAS_LANGUAGE]->(:Language {{code: $language_base}}) RETURN e
    }}
    ELSE {{ MATCH (e:Text) RETURN e }}
}}
WITH e
WHERE {has_language("e")}
  AND ($category_id IS NULL OR (e)-[:TEXT_OF]->(:Work)-[:HAS_CATEGORY]->(:Category {{id: $category_id}}))
  AND ($author_id IS NULL OR EXISTS {{
      (e)-[:HAS_CONTRIBUTION]->(:Contribution)-[:BY]->(:Person {{id: $author_id}})
  }})
  AND ALL(tag_group IN $tag_groups WHERE EXISTS {{
      (e)-[:TEXT_OF]->(:Work)-[:HAS_TAG]->(t:Tag) WHERE t.id IN tag_group
  }})
  AND ($wiki IS NULL OR e.wiki = $wiki)
  AND (size($licenses) = 0 OR EXISTS {{ (e)-[:HAS_LICENSE]->(l:LicenseType) WHERE l.name IN $licenses }})
"""
GET_ALL_QUERY: LiteralString = f"""
{_FILTERED_TEXTS}
ORDER BY e.id SKIP $offset LIMIT $limit
RETURN {_TEXT_RETURN}
"""
GET_LANGUAGES_QUERY: LiteralString = f"""
{_FILTERED_TEXTS}
MATCH (e)-[:HAS_LANGUAGE]->(l:Language)
RETURN l.code AS code, l.name AS name, count(DISTINCT e) AS text_count
ORDER BY code
"""
UPDATE_LICENSE_QUERY: LiteralString = replace_license("Text")
UPDATE_LANGUAGE_QUERY: LiteralString = replace_language("Text")
UPDATE_CATEGORY_QUERY: LiteralString = """
MATCH (w:Work {id: $work_id}), (cat:Category {id: $category_id})
OPTIONAL MATCH (w)-[r:HAS_CATEGORY]->()
DELETE r
WITH w, cat
MERGE (w)-[:HAS_CATEGORY]->(cat)
RETURN w.id as work_id
"""
_CREATE_TEXT_LINKS: LiteralString = """
MATCH (n:Nomen {id: $title_nomen_id}), (l:Language {code: $language_code})
MATCH (license:LicenseType {name: $license})
MERGE (e)-[:HAS_LANGUAGE {bcp47: $bcp47_tag}]->(l)
MERGE (e)-[:HAS_TITLE]->(n)
MERGE (e)-[:HAS_LICENSE]->(license)
RETURN e.id AS text_id, w.id AS work_id
"""
CREATE_STANDALONE_QUERY: LiteralString = f"""
OPTIONAL MATCH (whole:Text {{id: $target_id}})
CREATE (w:Work {{id: $work_id}})
CREATE (e:Text {{id: $text_id, bdrc: $bdrc, wiki: $wiki, date: $date}})
MERGE (e)-[:TEXT_OF {{original: $original}}]->(w)
CALL (*) {{ WHEN whole IS NOT NULL THEN {{ CREATE (e)-[:PART_OF]->(whole) }} }}
{_CREATE_TEXT_LINKS}
"""
CREATE_TRANSLATION_QUERY: LiteralString = f"""
MATCH (target:Text {{id: $target_id}})-[:TEXT_OF]->(w:Work)
CREATE (e:Text {{id: $text_id, bdrc: $bdrc, wiki: $wiki, date: $date}})
MERGE (e)-[:TEXT_OF {{original: false}}]->(w)
MERGE (e)-[:TRANSLATION_OF]->(target)
{_CREATE_TEXT_LINKS}
"""
CREATE_COMMENTARY_QUERY: LiteralString = f"""
MATCH (target:Text {{id: $target_id}})
CREATE (w:Work {{id: $work_id}})
CREATE (e:Text {{id: $text_id, bdrc: $bdrc, wiki: $wiki, date: $date}})
MERGE (e)-[:COMMENTARY_OF]->(target)
MERGE (e)-[:TEXT_OF {{original: true}}]->(w)
{_CREATE_TEXT_LINKS}
"""
GET_WORK_ID_QUERY: LiteralString = """
MATCH (e:Text {id: $text_id})-[:TEXT_OF]->(w:Work)
RETURN w.id AS work_id
"""
LOCK_WITH_WORK_QUERY: LiteralString = """
MATCH (e:Text {id: $id})-[:TEXT_OF]->(w:Work)
CALL apoc.lock.nodes([w, e])
FINISH
"""
DELETE_CHECK_QUERY: LiteralString = """
MATCH (e:Text {id: $text_id})-[:TEXT_OF]->(w:Work)
RETURN w.id AS work_id,
       count { (e)<-[:EDITION_OF]-(:Edition) } AS edition_count,
       count { (e)<-[:TRANSLATION_OF]-(:Text) } AS translation_count,
       count { (e)<-[:COMMENTARY_OF]-(:Text) } AS commentary_count,
       count { (e)<-[:PART_OF]-(:Text) } AS part_count,
       count { (e)<-[:RECITATION_OF]-(:Audio) } AS audio_count
"""
DELETE_QUERY: LiteralString = f"""
MATCH (e:Text {{id: $text_id}})-[:TEXT_OF]->(w:Work)
WITH e, w, count {{ (:Text)-[:TEXT_OF]->(w) }} AS work_text_count
{nomen.delete_names("e", "HAS_TITLE")}
CALL (e) {{
    OPTIONAL MATCH (e)-[:HAS_CONTRIBUTION]->(c:Contribution)
    DETACH DELETE c
}}
DETACH DELETE e
WITH w, work_text_count
CALL (*) {{
    WHEN work_text_count = 1 THEN {{ DETACH DELETE w }}
}}
RETURN work_text_count = 1 AS work_deleted
"""


@reading
async def get(tx: AsyncManagedTransaction, text_id: str, application: str | None = None) -> TextOutput:
    return await fetch_one(
        tx,
        GET_QUERY,
        TextOutput,
        f"Text with ID '{text_id}' not found",
        field="text",
        id=text_id,
        application=application,
    )


async def _validated_filter_params(
    tx: AsyncManagedTransaction, filters: TextFacetFilter, language: str | None = None
) -> dict[str, Any]:
    return {
        "language": language,
        "language_base": await language_database.validated_language_code(tx, language),
        "category_id": filters.category_id,
        "author_id": filters.author_id,
        "tag_groups": filters.tag_groups,
        "bdrc": filters.bdrc,
        "wiki": filters.wiki,
        "licenses": [license_type.value for license_type in filters.license],
    }


async def create_in_transaction(
    tx: AsyncManagedTransaction, text: TextInput, text_id: str | None = None, application: str | None = None
) -> str:
    text_id = text_id or generate_id()
    await _validate_related_text(tx, text)
    work_id = generate_id()
    await validate_text_creation(tx, text)
    language_code = await language_database.validated_language_code(tx, text.language)
    alt_titles = [title.root for title in text.alt_titles or []]
    title_nomen_id = await nomen.create(tx, text.title.root, alt_titles)
    params: dict[str, Any] = {
        "text_id": text_id,
        "bdrc": text.bdrc,
        "wiki": text.wiki,
        "date": text.date,
        "language_code": language_code,
        "bcp47_tag": text.language,
        "title_nomen_id": title_nomen_id,
        "target_id": text.translation_of or text.commentary_of or text.part_of,
        "license": text.license.value,
    }
    if text.commentary_of:
        result = await tx.run(CREATE_COMMENTARY_QUERY, work_id=work_id, **params)
    elif text.translation_of:
        result = await tx.run(CREATE_TRANSLATION_QUERY, **params)
    else:
        result = await tx.run(CREATE_STANDALONE_QUERY, work_id=work_id, original=True, **params)
    record = await result.single(strict=True)
    created_text_id = str(record["text_id"])
    work_id = str(record["work_id"])
    if text.category_id is not MISSING:
        await _set_category(tx, work_id, None, text.category_id, application=application, expected=None)
    await contribution.create_all(tx, TEXT_LABEL, created_text_id, text.contributions)
    if text.tag_ids:
        await tag.set_work_tags(tx, work_id, text.tag_ids, application=application)
    return created_text_id


async def create(db: SessionSource, text: TextInput, application: str | None = None) -> str:
    with on_constraint_violation():
        return await db.write(create_in_transaction, text, application=application)


async def lock_with_work(tx: AsyncManagedTransaction, text_id: str) -> None:
    """Lock a text and its Work, Work first, as every text mutation does."""
    await (await tx.run(LOCK_WITH_WORK_QUERY, id=text_id)).consume()


async def _get_for_update(tx: AsyncManagedTransaction, text_id: str, not_found: str) -> Record:
    await lock_with_work(tx, text_id)
    return await fetch_record(tx, GET_QUERY, not_found, id=text_id, application=None)


async def _validate_related_text(tx: AsyncManagedTransaction, text: TextInput) -> None:
    target_id = text.translation_of or text.commentary_of or text.part_of
    if not target_id:
        return
    record = await _get_for_update(tx, target_id, f"Target text '{target_id}' not found")
    target_language = record["text"]["language"]
    if text.translation_of and target_language == text.language:
        raise DataValidationError("Translation must have a different language than the target text")


async def _set_category(
    tx: AsyncManagedTransaction,
    work_id: str,
    current: str | None,
    category_id: str,
    *,
    application: str | None,
    expected: str | None,
) -> None:
    if current == category_id:
        return
    if current is not None:
        await category.validate_category_exists(tx, current, application)
        if expected != current:
            raise DataConflictError("Category changed or expected_category_id was not supplied")
    await category.validate_category_exists(tx, category_id, application)
    not_found = "Work or category no longer exists"
    await fetch_record(tx, UPDATE_CATEGORY_QUERY, not_found, work_id=work_id, category_id=category_id)


async def validate_text_creation(tx: AsyncManagedTransaction, text: TextInput) -> None:
    await validate_text_title_unique(tx, dict(text.title.root), text.language)
    await contribution.validate_contribution_references(tx, text.contributions)


async def validate_text_title_unique(tx: AsyncManagedTransaction, title: dict[str, str], text_language: str) -> None:
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
        raise DataValidationError(f"Text with title '{record['text']}' in language '{record['lang']}' already exists")


async def get_by_ids(db: SessionSource, text_ids: list[str], application: str | None = None) -> list[TextOutput]:
    if not text_ids:
        return []
    return await query_all(db, GET_BY_IDS_QUERY, TextOutput, field="text", ids=text_ids, application=application)


async def get_work_id(db: SessionSource, text_id: str) -> str:
    not_found = f"Text with ID '{text_id}' not found or has no associated Work"
    return (await query_record(db, GET_WORK_ID_QUERY, not_found, text_id=text_id))["work_id"]


async def get_work_text_ids(db: SessionSource, source: Literal["work", "text"], source_id: str) -> list[str]:
    """List texts in the work identified by its own ID or a member text's ID."""
    records = await db.query(
        """
            MATCH (t:Text)-[:TEXT_OF]->(w:Work)
            WHERE w.id = $work_id OR EXISTS { (:Text {id: $text_id})-[:TEXT_OF]->(w) }
            RETURN t.id AS id
            """,
        work_id=source_id if source == "work" else None,
        text_id=source_id if source == "text" else None,
    )
    return [record["id"] for record in records]


@reading
async def get_all(
    tx: AsyncManagedTransaction,
    offset: int,
    limit: int,
    filters: TextFilter | None = None,
    application: str | None = None,
) -> list[TextOutput]:
    filters = filters or TextFilter()
    params = await _validated_filter_params(tx, filters, filters.language)
    return await fetch_all(
        tx, GET_ALL_QUERY, TextOutput, field="text", offset=offset, limit=limit, application=application, **params
    )


@reading
async def get_languages(tx: AsyncManagedTransaction, filters: TextFacetFilter) -> list[TextLanguageResponse]:
    params = await _validated_filter_params(tx, filters)
    return await fetch_all(tx, GET_LANGUAGES_QUERY, TextLanguageResponse, **params)


@writing
async def delete(tx: AsyncManagedTransaction, text_id: str) -> None:
    await lock_with_work(tx, text_id)
    not_found = f"Text with ID '{text_id}' not found"
    record = await fetch_record(tx, DELETE_CHECK_QUERY, not_found, text_id=text_id)
    raise_if_blockers(
        f"Text '{text_id}' cannot be deleted because it has",
        {
            "edition(s)": record["edition_count"],
            "translation(s)": record["translation_count"],
            "commentary/commentaries": record["commentary_count"],
            "part(s)": record["part_count"],
            "audio(s)": record["audio_count"],
        },
    )
    await tx.run(DELETE_QUERY, text_id=text_id)


async def update(db: SessionSource, text_id: str, patch: TextPatch, application: str | None = None) -> TextOutput:

    async def update_transaction(tx: AsyncManagedTransaction) -> TextOutput:
        record = await _get_for_update(tx, text_id, f"Text with ID '{text_id}' not found")
        updated = TextBase.model_validate(record["text"] | patch.model_dump(), extra="ignore")
        await set_properties(tx, TEXT_LABEL, text_id, patch.model_dump(include={"bdrc", "wiki", "date"}))
        if patch.model_fields_set & {"title", "alt_titles"}:
            aliases = [title.root for title in updated.alt_titles or []]
            await nomen.replace(tx, TEXT_LABEL, text_id, updated.title.root, alternative_texts=aliases)
        if patch.license is not MISSING:
            await tx.run(UPDATE_LICENSE_QUERY, id=text_id, license=patch.license.value)
        if patch.language is not MISSING:
            await tx.run(
                UPDATE_LANGUAGE_QUERY,
                id=text_id,
                language_code=base_language_code(patch.language),
                bcp47=patch.language,
            )
        if patch.category_id is not MISSING:
            await _set_category(
                tx,
                record["work_id"],
                record["text"]["category_id"],
                patch.category_id,
                application=application,
                expected=None if patch.expected_category_id is MISSING else patch.expected_category_id,
            )
        if patch.contributions is not MISSING:
            await contribution.replace(tx, TEXT_LABEL, text_id, patch.contributions)
        if patch.tag_ids is not MISSING:
            await tag.set_work_tags(tx, record["work_id"], patch.tag_ids, application=application, replace=True)
        return await get(tx, text_id, application)

    with on_constraint_violation():
        return await db.write(update_transaction)
