# pylint: disable=redefined-outer-name
"""
Integration tests for /v2/languages endpoints using real Neo4j test instance.

Tests endpoints:
- GET /v2/languages
- POST /v2/languages
"""

import pytest


@pytest.mark.asyncio(loop_scope="session")
class TestLanguagesEndpoints:
    """Integration tests for /v2/languages endpoints"""


class TestGetAllLanguages(TestLanguagesEndpoints):
    """Tests for GET /v2/languages"""

    async def test_get_all_languages_success(self, client, test_database):
        """Test successful retrieval of all languages"""
        response = await client.get("/v2/languages")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 5

        codes = {lang["code"] for lang in data}
        assert "bo" in codes
        assert "en" in codes
        assert "sa" in codes
        assert "zh" in codes

    async def test_get_all_languages_contains_expected_fields(self, client, test_database):
        """Test that each language has code and name fields"""
        response = await client.get("/v2/languages")

        assert response.status_code == 200
        data = response.json()

        for lang in data:
            assert "code" in lang
            assert "name" in lang
            assert isinstance(lang["code"], str)
            assert isinstance(lang["name"], str)

    async def test_get_all_languages_ordered_by_code(self, client, test_database):
        """Test that languages are returned ordered by code"""
        response = await client.get("/v2/languages")

        assert response.status_code == 200
        data = response.json()
        codes = [lang["code"] for lang in data]
        assert codes == sorted(codes)


class TestCreateLanguage(TestLanguagesEndpoints):
    """Tests for POST /v2/languages"""

    async def test_create_language_success(self, client, test_database):
        """Test successful language creation"""
        language_data = {"code": "fr", "name": "French"}

        response = await client.post("/v2/languages", json=language_data)

        assert response.status_code == 201
        data = response.json()
        assert data["code"] == "fr"
        assert data["name"] == "French"

    async def test_create_language_appears_in_get_all(self, client, test_database):
        """Test that created language appears in GET all"""
        language_data = {"code": "de", "name": "German"}

        post_response = await client.post("/v2/languages", json=language_data)
        assert post_response.status_code == 201

        get_response = await client.get("/v2/languages")
        assert get_response.status_code == 200
        data = get_response.json()
        codes = {lang["code"] for lang in data}
        assert "de" in codes

    async def test_create_language_duplicate_fails(self, client, test_database):
        """Test that creating a duplicate language fails"""
        language_data = {"code": "bo", "name": "Tibetan Duplicate"}

        response = await client.post("/v2/languages", json=language_data)

        assert response.status_code == 422
        assert "detail" in response.json()

    async def test_create_language_missing_code(self, client, test_database):
        """Test that creating a language without code fails"""
        language_data = {"name": "Missing Code Language"}

        response = await client.post("/v2/languages", json=language_data)

        assert response.status_code == 422
        assert "detail" in response.json()

    async def test_create_language_missing_name(self, client, test_database):
        """Test that creating a language without name fails"""
        language_data = {"code": "xx"}

        response = await client.post("/v2/languages", json=language_data)

        assert response.status_code == 422
        assert "detail" in response.json()

    async def test_create_language_empty_body(self, client, test_database):
        """Test that creating a language with empty body fails"""
        response = await client.post("/v2/languages", json={})

        assert response.status_code == 422
        assert "detail" in response.json()

    async def test_create_language_missing_body(self, client, test_database):
        """Test that creating a language without body fails"""
        response = await client.post("/v2/languages")

        assert response.status_code == 422
        assert "detail" in response.json()

    async def test_create_language_empty_code(self, client, test_database):
        """Test that creating a language with empty code fails"""
        language_data = {"code": "", "name": "Empty Code Language"}

        response = await client.post("/v2/languages", json=language_data)

        assert response.status_code == 422
        assert "detail" in response.json()

    async def test_create_language_empty_name(self, client, test_database):
        """Test that creating a language with empty name fails"""
        language_data = {"code": "yy", "name": ""}

        response = await client.post("/v2/languages", json=language_data)

        assert response.status_code == 422
        assert "detail" in response.json()

    async def test_create_language_malformed_json(self, client, test_database):
        """Test that creating a language with malformed JSON fails"""
        response = await client.post(
            "/v2/languages",
            content="{invalid json}",
            headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 422
        assert "detail" in response.json()
