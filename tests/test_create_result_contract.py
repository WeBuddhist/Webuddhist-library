"""Regression tests for Neo4j create-result handling.

Create methods must verify the identifiers returned by Neo4j rather than assuming
that submitted identifiers were persisted.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import MISSING

import database.annotation.single_span as single_span_module
from database import (
    api_key,
    application,
    contribution,
    language,
    nomen,
)
from database import (
    audio as audio_database,
)
from database import (
    category as category_database,
)
from database import (
    edition as edition_database,
)
from database import (
    person as person_database,
)
from database import (
    recording as recording_database,
)
from database import (
    tag as tag_database,
)
from database import (
    text as text_database,
)
from database.annotation import (
    bibliographic,
    mark,
    table_of_contents,
)
from database.annotation import (
    note as note_database,
)
from database.annotation import (
    pagination as pagination_database,
)
from database.annotation import (
    segmentation as segmentation_database,
)
from database.content_state import ContentState
from errors import DataNotFoundError, DataValidationError
from models.annotation import (
    BibliographicMetadataInput,
    FootnoteInput,
    FootnoteOutput,
    MarkInput,
    NoteInput,
    NoteOutput,
    Page,
    PaginationInput,
    SegmentationInput,
    SegmentInput,
    Span,
    Volume,
)
from models.edition import EditionInput
from models.enums import AudioFormat, BibliographyType, MarkType, NoteType


class TrackingResult:
    def __init__(self, record):
        self.record = record
        self.single_strict_args = []

    async def data(self):
        return []

    async def consume(self):
        return None

    async def single(self, strict=False):
        self.single_strict_args.append(strict)
        return self.record


class FakeTransaction:
    def __init__(self, results_by_query, default_record=None):
        self.results_by_query = results_by_query
        self.default_record = default_record

    async def run(self, query, **_params):
        result = self.results_by_query.get(query)
        if result is not None:
            return result
        return TrackingResult(self.default_record)


class FakeDatabase:
    def __init__(self, tx):
        self.tx = tx

    async def read(self, work, *args, **kwargs):
        return await work(self.tx, *args, **kwargs)

    async def write(self, work, *args, **kwargs):
        return await work(self.tx, *args, **kwargs)

    async def query(self, query, *, write=False, **params):
        result = await self.tx.run(query, **params)
        return await result.data()


def assert_strict_neo4j_id(result, returned_id, field):
    assert result.single_strict_args == [True]
    assert returned_id == result.record[field]


@pytest.fixture
def skip_span_validation(monkeypatch):
    state = ContentState("edition-id", "text-id", "key", 1, 0, is_excerpt=False)
    for module in (single_span_module, pagination_database, segmentation_database, table_of_contents):
        monkeypatch.setattr(module, "touch_edition", AsyncMock(return_value=state))


async def test_note_create_strictly_returns_neo4j_id(monkeypatch, skip_span_validation):
    monkeypatch.setattr(single_span_module, "generate_id", lambda: "submitted-note-id")
    create_result = TrackingResult({"id": "neo4j-note-id"})
    tx = FakeTransaction({note_database.CREATE_QUERY: create_result})
    returned_id = await note_database.add(
        tx, "edition-id", NoteInput(span=Span(start=0, end=1), text="note"), "durchen"
    )
    assert_strict_neo4j_id(create_result, returned_id, "id")


class RecordingTransaction:
    def __init__(self, record=None):
        self.record = record
        self.calls = []

    async def run(self, query, parameters=None, **params):
        self.calls.append((query, {**(parameters or {}), **params}))
        return TrackingResult(self.record)


@pytest.mark.parametrize("position", [0, 1])
async def test_footnote_create_stores_empty_span_with_note_type(monkeypatch, skip_span_validation, position):
    monkeypatch.setattr(single_span_module, "generate_id", lambda: "submitted-footnote-id")
    tx = RecordingTransaction({"id": "neo4j-footnote-id"})
    returned_id = await note_database.add_footnote(tx, "edition-id", FootnoteInput(position=position, text="footnote"))
    assert returned_id == "neo4j-footnote-id"
    [(query, params)] = tx.calls
    assert query == note_database.CREATE_QUERY
    assert (params["span_start"], params["span_end"]) == (position, position)
    assert params["note_type"] == "footnote"


async def test_footnote_create_rejects_position_beyond_content(monkeypatch):
    state = ContentState("edition-id", "text-id", "key", 5, 0, is_excerpt=False)
    monkeypatch.setattr(single_span_module, "touch_edition", AsyncMock(return_value=state))
    tx = RecordingTransaction()
    with pytest.raises(DataValidationError):
        await note_database.add_footnote(tx, "edition-id", FootnoteInput(position=6, text="footnote"))
    assert tx.calls == []


@pytest.mark.parametrize(
    "query",
    [
        note_database.GET_BY_ID_QUERY,
        note_database.GET_BY_EDITION_ID_QUERY,
        note_database.GET_EDITION_ID_QUERY,
        note_database.DELETE_QUERY,
        note_database.DELETE_ALL_BY_TYPE_QUERY,
    ],
)
def test_note_queries_filter_by_note_type(query):
    assert "NoteType {name: $note_type}" in query


def test_footnote_output_takes_position_from_note_span():
    note = NoteOutput(
        id="note-id",
        edition_id="edition-id",
        text_id="text-id",
        span=Span(start=7, end=7),
        text="footnote",
        metadata={"name": "Citation"},
    )
    footnote = FootnoteOutput.from_note(note)
    assert footnote.model_dump() == {
        "id": "note-id",
        "edition_id": "edition-id",
        "text_id": "text-id",
        "position": 7,
        "text": "footnote",
        "metadata": {"name": "Citation"},
    }


async def test_delete_note_touches_its_edition_then_deletes(monkeypatch):
    touch = AsyncMock()
    monkeypatch.setattr(note_database, "touch_edition", touch)
    tx = RecordingTransaction({"edition_id": "edition-id"})
    await note_database.delete(tx, "note-id", NoteType.FOOTNOTE)
    touch.assert_awaited_once_with(tx, "edition-id")
    params = {"note_id": "note-id", "note_type": "footnote"}
    assert tx.calls == [(note_database.GET_EDITION_ID_QUERY, params), (note_database.DELETE_QUERY, params)]


async def test_delete_note_of_other_type_touches_nothing(monkeypatch):
    touch = AsyncMock()
    monkeypatch.setattr(note_database, "touch_edition", touch)
    tx = RecordingTransaction(None)
    await note_database.delete(tx, "durchen-id", NoteType.FOOTNOTE)
    touch.assert_not_awaited()
    assert tx.calls == [(note_database.GET_EDITION_ID_QUERY, {"note_id": "durchen-id", "note_type": "footnote"})]


async def test_delete_all_footnotes_touches_edition_and_sends_note_type(monkeypatch):
    touch = AsyncMock()
    monkeypatch.setattr(note_database, "touch_edition", touch)
    tx = RecordingTransaction()
    await note_database.delete_all_footnotes(FakeDatabase(tx), "edition-id")
    touch.assert_awaited_once_with(tx, "edition-id")
    assert tx.calls == [(note_database.DELETE_ALL_BY_TYPE_QUERY, {"edition_id": "edition-id", "note_type": "footnote"})]


async def test_delete_all_footnotes_raises_when_edition_missing(monkeypatch):
    monkeypatch.setattr(note_database, "touch_edition", AsyncMock(side_effect=DataNotFoundError("missing")))
    tx = RecordingTransaction()
    with pytest.raises(DataNotFoundError):
        await note_database.delete_all_footnotes(FakeDatabase(tx), "missing")
    assert tx.calls == []


async def test_bibliographic_create_strictly_returns_neo4j_id(monkeypatch, skip_span_validation):
    monkeypatch.setattr(single_span_module, "generate_id", lambda: "submitted-bibliographic-id")
    create_result = TrackingResult({"id": "neo4j-bibliographic-id"})
    tx = FakeTransaction({bibliographic.CREATE_QUERY: create_result})
    returned_id = await bibliographic.add(
        tx, "edition-id", BibliographicMetadataInput(span=Span(start=0, end=1), type=BibliographyType.COLOPHON)
    )
    assert_strict_neo4j_id(create_result, returned_id, "id")


async def test_mark_create_strictly_returns_neo4j_id(monkeypatch, skip_span_validation):
    monkeypatch.setattr(single_span_module, "generate_id", lambda: "submitted-mark-id")
    create_result = TrackingResult({"id": "neo4j-mark-id"})
    tx = FakeTransaction({mark.CREATE_QUERY: create_result})
    returned_id = await mark.add(tx, "edition-id", MarkInput(span=Span(start=0, end=1)), MarkType.YIGCHUNG)
    assert_strict_neo4j_id(create_result, returned_id, "id")


async def test_pagination_create_strictly_returns_neo4j_id(monkeypatch, skip_span_validation):
    monkeypatch.setattr(pagination_database, "generate_id", lambda: "submitted-pagination-id")
    create_result = TrackingResult({"id": "neo4j-pagination-id"})
    tx = FakeTransaction({pagination_database.CREATE_QUERY: create_result})
    pagination = PaginationInput(volumes=[Volume(pages=[Page(reference="1a", lines=[Span(start=0, end=1)])])])
    returned_id = await pagination_database.add(tx, "edition-id", pagination)
    assert_strict_neo4j_id(create_result, returned_id, "id")


async def test_segmentation_create_strictly_returns_neo4j_id(monkeypatch, skip_span_validation):
    monkeypatch.setattr(segmentation_database, "generate_id", lambda: "submitted-segmentation-id")
    create_result = TrackingResult({"id": "neo4j-segmentation-id"})
    tx = FakeTransaction({segmentation_database.CREATE_QUERY: create_result})
    segmentation = SegmentationInput(segments=[SegmentInput(lines=[Span(start=0, end=1)])])
    returned_id = await segmentation_database.add(tx, "edition-id", segmentation)
    assert_strict_neo4j_id(create_result, returned_id, "id")


async def test_table_of_contents_create_strictly_returns_neo4j_id(monkeypatch, skip_span_validation):
    monkeypatch.setattr(table_of_contents, "generate_id", lambda: "submitted-toc-id")
    monkeypatch.setattr(
        table_of_contents,
        "_flatten_sections",
        AsyncMock(return_value=[{"id": "section-id", "parent_id": None}]),
    )
    create_result = TrackingResult({"id": "neo4j-toc-id"})
    sections_result = TrackingResult({"count": 1})
    hierarchy_result = TrackingResult({"count": 0})
    tx = FakeTransaction(
        {
            table_of_contents.CREATE_TOC_QUERY: create_result,
            table_of_contents.CREATE_SECTIONS_QUERY: sections_result,
            table_of_contents.CREATE_HIERARCHY_QUERY: hierarchy_result,
        }
    )
    toc = SimpleNamespace(max_end=1, metadata=None, sections=[SimpleNamespace()])
    returned_id = await table_of_contents.add(tx, "edition-id", toc)
    assert_strict_neo4j_id(create_result, returned_id, "id")


@pytest.mark.parametrize("returned_ids", [["submitted-nomen-id"], [], ["unexpected-id"]])
async def test_nomen_create_verifies_persisted_ids(monkeypatch, returned_ids):
    monkeypatch.setattr(language, "validate_language_codes_exist", AsyncMock())
    monkeypatch.setattr(nomen, "generate_id", lambda: "submitted-nomen-id")
    create_result = SimpleNamespace(value=AsyncMock(return_value=returned_ids))
    tx = FakeTransaction({nomen.CREATE_MANY_QUERY: create_result})
    if returned_ids == ["submitted-nomen-id"]:
        assert await nomen.create(tx, {"en": "Title"}) == "submitted-nomen-id"
    else:
        with pytest.raises(RuntimeError, match="Incomplete nomen creation"):
            await nomen.create(tx, {"en": "Title"})


async def test_person_create_strictly_returns_neo4j_id(monkeypatch):
    monkeypatch.setattr(person_database, "generate_id", lambda: "submitted-person-id")
    monkeypatch.setattr(nomen, "create", AsyncMock(return_value="nomen-id"))
    create_result = TrackingResult({"person_id": "neo4j-person-id"})
    database = FakeDatabase(FakeTransaction({person_database.CREATE_QUERY: create_result}))
    person = SimpleNamespace(name=SimpleNamespace(root={"en": "Person"}), alt_names=None, bdrc=None, wiki=None)
    returned_id = await person_database.create(database, person)
    assert_strict_neo4j_id(create_result, returned_id, "person_id")


@pytest.mark.parametrize(
    ("translation_of", "commentary_of", "create_query"),
    [
        (None, None, text_database.CREATE_STANDALONE_QUERY),
        ("target-text-id", None, text_database.CREATE_TRANSLATION_QUERY),
        (None, "target-text-id", text_database.CREATE_COMMENTARY_QUERY),
    ],
)
async def test_text_create_strictly_returns_neo4j_id(monkeypatch, translation_of, commentary_of, create_query):
    monkeypatch.setattr(text_database, "_validate_related_text", AsyncMock())
    monkeypatch.setattr(text_database, "validate_text_creation", AsyncMock())
    monkeypatch.setattr(language, "validate_language_code_exists", AsyncMock())
    monkeypatch.setattr(category_database, "validate_category_exists", AsyncMock())
    monkeypatch.setattr(nomen, "create", AsyncMock(return_value="nomen-id"))
    monkeypatch.setattr(text_database, "generate_id", lambda: "submitted-work-id")
    create_result = TrackingResult({"text_id": "neo4j-text-id", "work_id": "neo4j-work-id"})
    tx = FakeTransaction({create_query: create_result})
    text = SimpleNamespace(
        language="en",
        alt_titles=None,
        title=SimpleNamespace(root={"en": "Text"}),
        bdrc=None,
        wiki=None,
        date=None,
        translation_of=translation_of,
        commentary_of=commentary_of,
        part_of=None,
        license=SimpleNamespace(value="public"),
        category_id=MISSING,
        contributions=[],
        tag_ids=[],
    )
    returned_id = await text_database.create_in_transaction(tx, text, "submitted-text-id")
    assert_strict_neo4j_id(create_result, returned_id, "text_id")


async def test_edition_create_strictly_returns_neo4j_id():
    create_result = TrackingResult({"edition_id": "neo4j-edition-id"})
    tx = FakeTransaction({edition_database.CREATE_QUERY: create_result}, default_record={"duplicate_critical": False})
    edition = EditionInput(type="critical")
    returned_id = await edition_database._create(tx, edition, "text-id", "submitted-edition-id", 1)
    assert_strict_neo4j_id(create_result, returned_id, "edition_id")


async def test_duplicate_critical_edition_fails_before_graph_writes():
    tx = RecordingTransaction({"duplicate_critical": True})
    edition = EditionInput(type="critical", incipit_title={"en": "Second edition"})
    with pytest.raises(DataValidationError, match="Critical edition already present for this text"):
        await edition_database.create(tx, edition, "second-id", "text-id", 1)
    assert all("CREATE" not in query and "MERGE" not in query for query, _ in tx.calls)


async def test_recording_create_strictly_returns_neo4j_id(monkeypatch):
    monkeypatch.setattr(recording_database, "validate_target", AsyncMock())
    create_result = TrackingResult({"recording_id": "neo4j-recording-id"})
    tx = FakeTransaction({recording_database.CREATE_QUERY: create_result})
    recording = SimpleNamespace(title=None, duration_ms=None, edition_id=None)
    returned_id = await recording_database.add(
        tx,
        "audio-id",
        recording,
        "submitted-recording-id",
        audio_key="objects/submitted-recording-id",
        audio_format=AudioFormat.MP3,
        size_bytes=10,
    )
    assert_strict_neo4j_id(create_result, returned_id, "recording_id")


async def test_audio_create_strictly_returns_neo4j_id(monkeypatch):
    monkeypatch.setattr(audio_database, "require_node", AsyncMock())
    monkeypatch.setattr(contribution, "validate_contribution_references", AsyncMock())
    create_result = TrackingResult({"audio_id": "neo4j-audio-id"})
    tx = FakeTransaction({audio_database.CREATE_QUERY: create_result})
    audio = SimpleNamespace(
        title=None, language=None, date=None, license=SimpleNamespace(value="public"), contributions=[]
    )
    returned_id = await audio_database.create(tx, "text-id", audio, "submitted-audio-id")
    assert_strict_neo4j_id(create_result, returned_id, "audio_id")


async def test_application_create_strictly_returns_neo4j_id():
    create_result = TrackingResult({"id": "neo4j-application-id"})
    database = FakeDatabase(FakeTransaction({application.CREATE_QUERY: create_result}))
    returned_id = await application.create(database, "submitted-application-id", "Application")
    assert_strict_neo4j_id(create_result, returned_id, "id")


async def test_language_create_strictly_returns_neo4j_id():
    create_result = TrackingResult({"code": "neo4j-language-code"})
    database = FakeDatabase(FakeTransaction({language.CREATE_QUERY: create_result}))
    returned_id = await language.create(database, "submitted-language-code", "Language")
    assert_strict_neo4j_id(create_result, returned_id, "code")


async def test_api_key_create_strictly_returns_neo4j_id(monkeypatch):
    monkeypatch.setattr(api_key, "_generate_api_key", lambda: "raw-key")
    create_result = TrackingResult({"id": "neo4j-key-id"})
    database = FakeDatabase(FakeTransaction({api_key.CREATE_QUERY: create_result}))
    returned_id, _raw_key = await api_key.create(database, "submitted-key-id", "Key", "key@example.com")
    assert_strict_neo4j_id(create_result, returned_id, "id")


async def test_category_create_keeps_strict_neo4j_id_contract(monkeypatch):
    create_result = TrackingResult({"category_id": "neo4j-category-id"})
    database = FakeDatabase(FakeTransaction({category_database.CREATE_QUERY: create_result}))
    monkeypatch.setattr(category_database, "_validate_not_exists", AsyncMock())
    monkeypatch.setattr(nomen, "create", AsyncMock(return_value="nomen-id"))
    category = SimpleNamespace(parent_id=None, title=SimpleNamespace(root={"en": "Category"}), description=None)
    returned_id = await category_database.create(database, category, "application-id")
    assert_strict_neo4j_id(create_result, returned_id, "category_id")


async def test_tag_create_keeps_strict_neo4j_id_contract(monkeypatch):
    create_result = TrackingResult({"tag_id": "neo4j-tag-id"})
    database = FakeDatabase(FakeTransaction({tag_database.CREATE_QUERY: create_result}))
    monkeypatch.setattr(tag_database, "_validate_not_exists", AsyncMock())
    monkeypatch.setattr(nomen, "create", AsyncMock(return_value="nomen-id"))
    tag = SimpleNamespace(title=SimpleNamespace(root={"en": "Tag"}), description=None)
    returned_id = await tag_database.create(database, tag, "application-id")
    assert_strict_neo4j_id(create_result, returned_id, "tag_id")
