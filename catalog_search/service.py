from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import aclosing
from typing import TYPE_CHECKING

from database.database_validator import DatabaseValidator
from exceptions import DataNotFoundError
from models.base import base_language_code
from models.contribution import PersonContributionOutput
from models.person import PersonOutput
from models.text import TextOutput
from search_client import SearchIndex, term_filters

from .mapping import catalog_index_body

if TYPE_CHECKING:
    from opensearchpy import AsyncOpenSearch

    from database import Database
    from models.requests import PersonFilter, TextFilter

PERSON_DOCUMENT_TYPE = "person"
TEXT_DOCUMENT_TYPE = "text"

# The sanskrit/tibetan analyzers emit per-syllable tokens, so distinct names can
# share (or even reorder) the same syllables: "Śāntideva" -> [sa, nti, de, ba] and
# "Devaśānti" -> [de, ba, sa, nti] are anagrams. Person lookups therefore match the
# query as an ordered phrase, which keeps both syllable-neighbors and anagrams out.
# Titles are free-form multi-word phrases, so they instead allow ~25% of tokens to
# differ once past a 3-token query. Both also prefix-match the last word on the
# word-tokenized folded labels, where a full name is never a prefix of an anagram.
_TEXT_MINIMUM_SHOULD_MATCH = "3<75%"


class CatalogSearchService:
    def __init__(self, *, client: AsyncOpenSearch, index_name: str) -> None:
        self.index = SearchIndex(client, index_name)

    async def setup_index(self) -> None:
        await self.index.setup_index(catalog_index_body())

    async def prepare_document(self, kind: str, entity_id: str, db: Database) -> dict:
        try:
            if kind == PERSON_DOCUMENT_TYPE:
                return _person_document(await db.person.get(entity_id))
            return _text_document(await db.text.get(entity_id))
        except DataNotFoundError:
            return {"id": _document_id(kind, entity_id), "document_type": kind, "deleted": True}

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
            hydrate=db.person.get_by_ids,
        )

    async def search_texts(
        self,
        *,
        db: Database,
        query: str,
        filters: TextFilter,
        offset: int,
        limit: int,
        application: str | None = None,
    ) -> list[TextOutput]:
        if filters.language:
            await db.transactions.execute_read(DatabaseValidator.validated_language_code, filters.language)
        return await self._search(
            query,
            TEXT_DOCUMENT_TYPE,
            _text_filters(filters),
            offset=offset,
            limit=limit,
            hydrate=lambda ids: db.text.get_by_ids(ids, application=application),
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
    fields = _search_fields()
    return {
        "size": limit,
        "sort": [{"_score": "desc"}, {"id": "asc"}],
        "query": {
            "bool": {
                "filter": [{"term": {"document_type": document_type}}, *filters],
                "must": [
                    {
                        "bool": {
                            "should": _match_clauses(query=query, document_type=document_type, fields=fields),
                            "minimum_should_match": 1,
                        }
                    }
                ],
            }
        },
    }


def _match_clauses(*, query: str, document_type: str, fields: list[str]) -> list[dict]:
    phrase = {"multi_match": {"query": query, "type": "phrase", "fields": fields, "boost": 4}}
    prefix = {"match_phrase_prefix": {"search_labels_folded": query}}
    if document_type == PERSON_DOCUMENT_TYPE:
        return [phrase, prefix]
    return [
        phrase,
        {
            "multi_match": {
                "query": query,
                "type": "best_fields",
                "fields": fields,
                "minimum_should_match": _TEXT_MINIMUM_SHOULD_MATCH,
            }
        },
        prefix,
    ]


def _search_fields() -> list[str]:
    return [
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
    ]


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
