from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import aclosing
from typing import TYPE_CHECKING

from opensearchpy import Q, Search

from database import language as language_database
from database import person as person_database
from database import text as text_database
from database.repository import iter_id_batches
from errors import DataNotFoundError
from models.base import base_language_code
from models.contribution import PersonContributionOutput
from models.person import PersonOutput
from models.text import TextOutput
from search.catalog_mapping import catalog_index_body
from search.client import SearchIndex, term_filters

if TYPE_CHECKING:
    from opensearchpy import AsyncOpenSearch

    from database import Database
    from models.requests import PersonFilter, TextFilter

PERSON_DOCUMENT_TYPE = "person"
TEXT_DOCUMENT_TYPE = "text"
_TEXT_MINIMUM_SHOULD_MATCH = "3<75%"
_SEARCH_FIELDS = (
    "primary_labels^6",
    "alt_labels^3",
    "search_labels_bo^4",
    "search_labels_bo.phonetic^3",
    "search_labels_bo.ewts^3",
    "search_labels_bo_ewts^4",
    "search_labels_bo_ewts.phonetic^3",
    "search_labels_sa^4",
    "search_labels_sa_deva^3",
    "search_labels_plain^3",
    "search_labels_folded^2",
)


class CatalogSearchService:
    def __init__(self, *, client: AsyncOpenSearch, index_name: str) -> None:
        self.index = SearchIndex(client, index_name)

    async def setup_index(self) -> None:
        await self.index.setup_index(catalog_index_body())

    async def prepare_document(self, kind: str, entity_id: str, db: Database) -> dict:
        try:
            if kind == PERSON_DOCUMENT_TYPE:
                return _person_document(await person_database.get(db, entity_id))
            return _text_document(await text_database.get(db, entity_id))
        except DataNotFoundError:
            return {"id": _document_id(kind, entity_id), "document_type": kind, "deleted": True}

    async def populate_index(self, db: Database) -> int:
        """Fill an empty replacement index during a write pause."""
        indexed = 0
        async for rows in iter_id_batches(db, "MATCH (p:Person) WHERE p.id > $after RETURN p.id AS id"):
            people = await person_database.get_by_ids(db, [row["id"] for row in rows])
            indexed += await self.index.bulk_index(map(_person_document, people), refresh=False)
        # A canonical text has an owning Work. Older graphs can contain texts without one.
        text_query = "MATCH (t:Text) WHERE t.id > $after AND EXISTS { (t)-[:TEXT_OF]->(:Work) } RETURN t.id AS id"
        async for rows in iter_id_batches(db, text_query):
            texts = await text_database.get_by_ids(db, [row["id"] for row in rows])
            indexed += await self.index.bulk_index(map(_text_document, texts), refresh=False)
        return indexed

    async def index_entity(self, kind: str, entity_id: str, db: Database) -> None:
        await self.index.publish(await self.prepare_document(kind, entity_id, db))

    async def search_persons(
        self, *, db: Database, query: str, filters: PersonFilter, offset: int, limit: int
    ) -> list[PersonOutput]:
        return await self._search(
            query,
            PERSON_DOCUMENT_TYPE,
            term_filters(bdrc=filters.bdrc, wiki=filters.wiki),
            offset=offset,
            limit=limit,
            hydrate=lambda ids: person_database.get_by_ids(db, ids),
        )

    async def search_texts(
        self, *, db: Database, query: str, filters: TextFilter, offset: int, limit: int, application: str | None = None
    ) -> list[TextOutput]:
        if filters.language:
            await language_database.validated_language_code(db, filters.language)

        async def hydrate(ids: list[str]) -> list[TextOutput]:
            texts = await text_database.get_by_ids(db, ids, application=application)
            return [text for text in texts if not filters.license or text.license in filters.license]

        return await self._search(
            query, TEXT_DOCUMENT_TYPE, _text_filters(filters), offset=offset, limit=limit, hydrate=hydrate
        )

    async def _search[T: (PersonOutput, TextOutput)](
        self,
        query: str,
        kind: str,
        filters: list[dict],
        *,
        offset: int,
        limit: int,
        hydrate: Callable[[list[str]], Awaitable[list[T]]],
    ) -> list[T]:
        body = _search_body(query=query, document_type=kind, limit=100, filters=filters)
        items: list[T] = []
        skipped = scanned = 0
        async with aclosing(self.index.pages(body)) as pages:
            async for hits in pages:
                scanned += len(hits)
                ids = [hit["_source"][f"{kind}_id"] for hit in hits]
                for item in await hydrate(ids):
                    if skipped < offset:
                        skipped += 1
                    else:
                        items.append(item)
                        if len(items) == limit:
                            return items
                if scanned >= 10000:
                    return items
        return items


def _person_document(person: PersonOutput) -> dict:
    labels = _build_label_fields(person.name.root, [alt.root for alt in person.alt_names or []])
    return {
        "id": _document_id(PERSON_DOCUMENT_TYPE, person.id),
        "document_type": PERSON_DOCUMENT_TYPE,
        "person_id": person.id,
        "bdrc": person.bdrc,
        "wiki": person.wiki,
        **labels,
    }


def _text_document(text: TextOutput) -> dict:
    labels = _build_label_fields(text.title.root, [alt.root for alt in text.alt_titles or []])
    return {
        "id": _document_id(TEXT_DOCUMENT_TYPE, text.id),
        "document_type": TEXT_DOCUMENT_TYPE,
        "text_id": text.id,
        "bdrc": text.bdrc,
        "wiki": text.wiki,
        "language": text.language.lower(),
        "category_id": text.category_id,
        "tag_ids": text.tag_ids,
        "contributor_ids": _contributor_ids(text),
        **labels,
    }


def _build_label_fields(primary: dict[str, str], alternatives: list[dict[str, str]]) -> dict[str, list[str]]:
    primary_labels = list(primary.values())
    alt_labels = [label for alt in alternatives for label in alt.values()]
    fields: dict[str, list[str]] = {
        "primary_labels": primary_labels,
        "alt_labels": alt_labels,
        "search_labels_folded": primary_labels + alt_labels,
        "search_labels_bo": [],
        "search_labels_bo_ewts": [],
        "search_labels_sa": [],
        "search_labels_sa_deva": [],
        "search_labels_plain": [],
    }
    for labels in (primary, *alternatives):
        for lang, label in labels.items():
            if target := _search_field_for_language(lang):
                fields[target].append(label)
    return {key: _dedupe(value) for key, value in fields.items()}


def _search_field_for_language(language: str) -> str | None:
    normalized = language.casefold()
    base = base_language_code(normalized)
    if base == "bo":
        if "ewts" in normalized or "wylie" in normalized:
            return "search_labels_bo_ewts"
        return "search_labels_bo"
    if base in {"sa", "pi"}:
        if "deva" in normalized:
            return "search_labels_sa_deva"
        return "search_labels_sa"
    if base == "en" or any(token in normalized for token in ("latin", "iast", "roman", "alalc")):
        return "search_labels_plain"
    return None


def _contributor_ids(text: TextOutput) -> list[str]:
    return _dedupe(
        [
            contribution.id
            for contribution in text.contributions
            if isinstance(contribution, PersonContributionOutput) and contribution.id is not None
        ]
    )


def _search_body(*, query: str, document_type: str, limit: int, filters: list[dict]) -> dict:
    matches = [Q("multi_match", query=query, type="phrase", fields=_SEARCH_FIELDS, boost=4)]
    if document_type != PERSON_DOCUMENT_TYPE:
        matches.append(
            Q(
                "multi_match",
                query=query,
                type="best_fields",
                fields=_SEARCH_FIELDS,
                minimum_should_match=_TEXT_MINIMUM_SHOULD_MATCH,
            )
        )
    matches.append(Q("match_phrase_prefix", search_labels_folded=query))
    search = Search().query(
        "bool",
        filter=[Q("term", document_type=document_type), *filters],
        must=[Q("bool", should=matches, minimum_should_match=1)],
    )
    return search.sort({"_score": "desc"}, {"id": "asc"}).extra(size=limit).to_dict()


def _text_filters(filters: TextFilter) -> list[dict]:
    filter_clauses = term_filters(category_id=filters.category_id, bdrc=filters.bdrc, wiki=filters.wiki)
    if language := filters.language:
        language_filter: dict = {"term": {"language": language}}
        if "-" not in language:
            language_filter = {"bool": {"should": [language_filter, {"prefix": {"language": language + "-"}}]}}
        filter_clauses.append(language_filter)
    filter_clauses.extend({"terms": {"tag_ids": tag_group}} for tag_group in filters.tag_groups)
    if filters.author_id:
        filter_clauses.append({"term": {"contributor_ids": filters.author_id}})
    return filter_clauses


def _document_id(document_type: str, entity_id: str) -> str:
    return f"{document_type}:{entity_id}"


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
