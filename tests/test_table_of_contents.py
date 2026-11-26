import pytest

from database import edition, person, text
from identifier import generate_id
from models.annotation import Page, PaginationInput, Span, Volume
from models.base import LocalizedString
from models.contribution import PersonContributionInput
from models.edition import EditionInput, EditionType
from models.enums import ContributorRole
from models.person import PersonInput
from models.text import TextInput


@pytest.fixture
def test_person_data() -> PersonInput:
    return PersonInput(name=LocalizedString({"en": "Table Of Contents Test Author"}), bdrc=f"P{generate_id()[:8]}")


async def _create_test_person(db, person_data: PersonInput) -> str:
    return await person.create(db, person_data)


async def _create_test_text(db, person_id: str) -> str:
    text_data = TextInput(
        category_id="category",
        title=LocalizedString({"en": "Table Of Contents Test Text", "bo": "ས་བཅད་ཚོད་ལྟ།"}),
        language="bo",
        contributions=[PersonContributionInput(type="person", id=person_id, role=ContributorRole.AUTHOR)],
    )
    return await text.create(db, text_data)


async def _create_test_edition(db, text_id: str) -> str:
    edition_id = generate_id()
    edition_data = EditionInput(
        type=EditionType.DIPLOMATIC, bdrc=f"W{edition_id[:8]}", source="Table Of Contents Test Source"
    )
    pagination = PaginationInput(
        volumes=[Volume(pages=[Page(reference="1a", lines=[Span(start=0, end=1)])])]
    )
    # Large enough for the section spans used across these tests; no base text is stored.
    await edition.create(db, edition_data, edition_id, text_id, content_length=1000, pagination=pagination)
    return edition_id


async def _create_test_graph(test_database, person_data: PersonInput) -> tuple[str, str]:
    person_id = await _create_test_person(test_database, person_data)
    text_id = await _create_test_text(test_database, person_id)
    edition_id = await _create_test_edition(test_database, text_id)
    return text_id, edition_id


def _table_of_contents_payload(name: str = "Main sa bcad") -> dict:
    return {
        "metadata": {"name": name},
        "sections": [
            {
                "title": {"en": "Chapter 1", "bo": "ལེའུ་དང་པོ།"},
                "summary": {"en": "Opening topic"},
                "span": {"start": 0, "end": 10},
                "subsections": [
                    {
                        "title": {"en": "Section 1.1"},
                        "summary": {"en": "Introductory topic"},
                        "span": {"start": 0, "end": 5},
                        "subsections": [
                            {
                                "title": {"en": "Subsection 1.1.1"},
                                "span": {"start": 0, "end": 2},
                            }
                        ],
                    },
                    {
                        "title": {"en": "Section 1.2"},
                        "span": {"start": 5, "end": 10},
                    },
                ],
            }
        ],
    }


async def _scalar(test_database, query: str, **params):
    async with test_database.get_session() as session:
        result = await session.run(query, **params)
        record = await result.single()
        return None if record is None else record[0]


def _count_sections(sections: list[dict]) -> int:
    total = len(sections)
    for section in sections:
        total += _count_sections(section.get("subsections", []))
    return total


def _nested_sanskrit_table_of_contents_payload() -> dict:
    subsections = [
        {
            "title": {"sa": f"Section {index}"},
            "span": {"start": start, "end": end},
        }
        for index, (start, end) in enumerate(
            [(23, 38), (79, 100), (120, 150), (180, 210), (240, 270), (300, 330), (360, 390), (420, 450), (480, 510), (540, 570), (600, 630)],
            start=1,
        )
    ]
    return {
        "sections": [
            {
                "title": {"sa": "बोधिचर्यावतारः।"},
                "span": {"start": 15, "end": 700},
                "subsections": subsections,
            }
        ]
    }


@pytest.mark.asyncio(loop_scope="session")
class TestTableOfContents:
    async def test_table_of_contents_round_trip_with_nested_sections(
        self, client, test_database, test_person_data
    ):
        text_id, edition_id = await _create_test_graph(test_database, test_person_data)

        post_response = await client.post(
            f"/v2/editions/{edition_id}/table-of-contents", json=_table_of_contents_payload()
        )
        assert post_response.status_code == 201, post_response.json()
        toc_id = post_response.json()["id"]

        get_response = await client.get(f"/v2/table-of-contents/{toc_id}")
        assert get_response.status_code == 200
        toc = get_response.json()
        assert toc["id"] == toc_id
        assert toc["edition_id"] == edition_id
        assert toc["text_id"] == text_id
        assert toc["metadata"]["name"] == "Main sa bcad"
        assert toc["sections"][0]["title"]["en"] == "Chapter 1"
        assert toc["sections"][0]["title"]["bo"] == "ལེའུ་དང་པོ།"
        assert toc["sections"][0]["summary"]["en"] == "Opening topic"
        assert toc["sections"][0]["subsections"][0]["summary"]["en"] == "Introductory topic"
        assert toc["sections"][0]["subsections"][0]["subsections"][0]["title"]["en"] == "Subsection 1.1.1"
        assert toc["sections"][0]["subsections"][1]["title"]["en"] == "Section 1.2"

        list_response = await client.get(f"/v2/editions/{edition_id}/table-of-contents")
        assert list_response.status_code == 200
        assert [item["id"] for item in list_response.json()] == [toc_id]

        delete_response = await client.delete(f"/v2/table-of-contents/{toc_id}")
        assert delete_response.status_code == 204
        assert (await client.get(f"/v2/table-of-contents/{toc_id}")).status_code == 404
        assert (await client.delete(f"/v2/table-of-contents/{toc_id}")).status_code == 204

    async def test_multiple_tables_of_contents_can_attach_to_one_edition(
        self, client, test_database, test_person_data
    ):
        _, edition_id = await _create_test_graph(test_database, test_person_data)

        first = await client.post(
            f"/v2/editions/{edition_id}/table-of-contents", json=_table_of_contents_payload("First toc")
        )
        second = await client.post(
            f"/v2/editions/{edition_id}/table-of-contents", json=_table_of_contents_payload("Second toc")
        )

        assert first.status_code == 201, first.json()
        assert second.status_code == 201, second.json()
        response = await client.get(f"/v2/editions/{edition_id}/table-of-contents")
        assert response.status_code == 200
        names = {toc["metadata"]["name"] for toc in response.json()}
        assert names == {"First toc", "Second toc"}

    @pytest.mark.parametrize("method", ["GET", "POST"])
    async def test_table_of_contents_edition_not_found(self, client, method):
        body = {"json": _table_of_contents_payload()} if method == "POST" else {}
        response = await client.request(method, "/v2/editions/nonexistent_id/table-of-contents", **body)
        assert response.status_code == 404
        assert response.json()["detail"] == "Edition with ID 'nonexistent_id' not found"

    async def test_table_of_contents_creates_span_of_for_every_section(
        self, client, test_database, test_person_data
    ):
        _, edition_id = await _create_test_graph(test_database, test_person_data)
        payload = _table_of_contents_payload()

        post_response = await client.post(f"/v2/editions/{edition_id}/table-of-contents", json=payload)
        assert post_response.status_code == 201, post_response.json()
        toc_id = post_response.json()["id"]

        section_count = _count_sections(payload["sections"])
        assert section_count == 4

        span_count = await _scalar(
            test_database,
            """
            MATCH (section:TableOfContentsSection)-[:SECTION_OF]->(:TableOfContents {id: $toc_id})
            MATCH (:Span)-[:SPAN_OF]->(section)
            RETURN count(section)
            """,
            toc_id=toc_id,
        )
        assert span_count == section_count

        sections_without_span = await _scalar(
            test_database,
            """
            MATCH (section:TableOfContentsSection)-[:SECTION_OF]->(:TableOfContents {id: $toc_id})
            WHERE NOT EXISTS { (:Span)-[:SPAN_OF]->(section) }
            RETURN count(section)
            """,
            toc_id=toc_id,
        )
        assert sections_without_span == 0

    async def test_table_of_contents_creates_span_of_for_deeply_nested_sections(
        self, client, test_database, test_person_data
    ):
        _, edition_id = await _create_test_graph(test_database, test_person_data)
        payload = _nested_sanskrit_table_of_contents_payload()

        post_response = await client.post(f"/v2/editions/{edition_id}/table-of-contents", json=payload)
        assert post_response.status_code == 201, post_response.json()
        toc_id = post_response.json()["id"]

        section_count = _count_sections(payload["sections"])
        assert section_count == 12

        span_count = await _scalar(
            test_database,
            """
            MATCH (section:TableOfContentsSection)-[:SECTION_OF]->(:TableOfContents {id: $toc_id})
            MATCH (:Span)-[:SPAN_OF]->(section)
            RETURN count(section)
            """,
            toc_id=toc_id,
        )
        assert span_count == section_count

        sections_without_span = await _scalar(
            test_database,
            """
            MATCH (section:TableOfContentsSection)-[:SECTION_OF]->(:TableOfContents {id: $toc_id})
            WHERE NOT EXISTS { (:Span)-[:SPAN_OF]->(section) }
            RETURN count(section)
            """,
            toc_id=toc_id,
        )
        assert sections_without_span == 0

    async def test_add_table_of_contents_rejects_subsection_outside_parent_span(
        self, client, test_database, test_person_data
    ):
        _, edition_id = await _create_test_graph(test_database, test_person_data)
        toc = _table_of_contents_payload()
        toc["sections"][0]["subsections"][0]["span"] = {"start": 9, "end": 12}

        response = await client.post(f"/v2/editions/{edition_id}/table-of-contents", json=toc)

        assert response.status_code == 422
        assert "contained" in str(response.json()).lower()

    async def test_add_table_of_contents_accepts_heading_without_content(
        self, client, test_database, test_person_data
    ):
        """A heading that carries no text of its own is an empty span anchored at its position."""
        _, edition_id = await _create_test_graph(test_database, test_person_data)
        toc = {
            "sections": [
                {"title": {"sa": "Dukaniddeso"}, "span": {"start": 40, "end": 40}},
                {
                    "title": {"sa": "Upādābhājanīyaṃ"},
                    "span": {"start": 40, "end": 80},
                    "subsections": [{"title": {"sa": "Empty subheading"}, "span": {"start": 80, "end": 80}}],
                },
            ]
        }

        post_response = await client.post(f"/v2/editions/{edition_id}/table-of-contents", json=toc)
        assert post_response.status_code == 201, post_response.json()

        sections = (await client.get(f"/v2/table-of-contents/{post_response.json()['id']}")).json()["sections"]
        # The empty heading sorts ahead of the sibling that starts where it sits
        assert [section["title"]["sa"] for section in sections] == ["Dukaniddeso", "Upādābhājanīyaṃ"]
        assert sections[0]["span"] == {"start": 40, "end": 40}
        assert sections[1]["subsections"][0]["span"] == {"start": 80, "end": 80}

    async def test_add_table_of_contents_rejects_overlapping_sibling_sections(
        self, client, test_database, test_person_data
    ):
        _, edition_id = await _create_test_graph(test_database, test_person_data)
        toc = {
            "sections": [
                {"title": {"en": "First"}, "span": {"start": 0, "end": 60}},
                {"title": {"en": "Second"}, "span": {"start": 40, "end": 80}},
            ]
        }

        response = await client.post(f"/v2/editions/{edition_id}/table-of-contents", json=toc)

        assert response.status_code == 422
        assert "overlap" in str(response.json()).lower()

    async def test_add_table_of_contents_rejects_unsorted_sibling_sections(
        self, client, test_database, test_person_data
    ):
        _, edition_id = await _create_test_graph(test_database, test_person_data)
        toc = {
            "sections": [
                {"title": {"en": "Second"}, "span": {"start": 40, "end": 80}},
                {"title": {"en": "First"}, "span": {"start": 0, "end": 40}},
            ]
        }

        response = await client.post(f"/v2/editions/{edition_id}/table-of-contents", json=toc)

        assert response.status_code == 422
        assert "sorted" in str(response.json()).lower()

    async def test_add_table_of_contents_rejects_inverted_span(self, client, test_database, test_person_data):
        _, edition_id = await _create_test_graph(test_database, test_person_data)
        toc = {"sections": [{"title": {"en": "Backwards"}, "span": {"start": 80, "end": 40}}]}

        response = await client.post(f"/v2/editions/{edition_id}/table-of-contents", json=toc)

        assert response.status_code == 422
        assert "greater" in str(response.json()).lower()

    async def test_delete_edition_cascades_tables_of_contents(self, client, test_database, test_person_data):
        _, edition_id = await _create_test_graph(test_database, test_person_data)
        post_response = await client.post(
            f"/v2/editions/{edition_id}/table-of-contents", json=_table_of_contents_payload()
        )
        assert post_response.status_code == 201, post_response.json()
        toc_id = post_response.json()["id"]

        await edition.delete(test_database, edition_id)

        assert (await client.get(f"/v2/table-of-contents/{toc_id}")).status_code == 404
        assert (
            await _scalar(test_database, "RETURN EXISTS { (:TableOfContents {id: $toc_id}) }", toc_id=toc_id) is False
        )
        assert (
            await _scalar(
                test_database,
                "MATCH (:TableOfContentsSection)-[:SECTION_OF]->(:TableOfContents {id: $toc_id}) RETURN count(*)",
                toc_id=toc_id,
            )
            == 0
        )

    async def test_patch_content_adjusts_table_of_contents_section_spans(self, client, test_database):
        person_response = await client.post("/v2/persons", json={"name": {"en": "Table Of Contents Patch Author"}})
        assert person_response.status_code == 201, person_response.json()
        person_id = person_response.json()["id"]
        text_response = await client.post(
            "/v2/texts",
            json={
                "title": {"en": "Table Of Contents Patch Text"},
                "language": "en",
                "category_id": "category",
                "contributions": [{"type": "person", "id": person_id, "role": "author"}],
            },
        )
        assert text_response.status_code == 201, text_response.json()
        text_id = text_response.json()["id"]
        edition_response = await client.post(
            f"/v2/texts/{text_id}/editions",
            json={
                "content": "0123456789",
                "metadata": {
                    "type": "diplomatic",
                    "bdrc": f"W{generate_id()[:8]}",
                    "source": "Table Of Contents Patch Source",
                },
                "pagination": {
                    "volumes": [{"pages": [{"reference": "1a", "lines": [{"start": 0, "end": 10}]}]}],
                },
            },
        )
        assert edition_response.status_code == 201, edition_response.json()
        edition_id = edition_response.json()["id"]
        toc_data = {
            "sections": [
                {
                    "title": {"en": "Middle section"},
                    "span": {"start": 2, "end": 5},
                }
            ]
        }
        toc_response = await client.post(f"/v2/editions/{edition_id}/table-of-contents", json=toc_data)
        assert toc_response.status_code == 201, toc_response.json()
        toc_id = toc_response.json()["id"]

        patch_response = await client.patch(
            f"/v2/editions/{edition_id}/content",
            json={"type": "insert", "position": 3, "text": "XX"},
        )
        assert patch_response.status_code == 204

        toc = (await client.get(f"/v2/table-of-contents/{toc_id}")).json()
        assert toc["sections"][0]["span"] == {"start": 2, "end": 7}
