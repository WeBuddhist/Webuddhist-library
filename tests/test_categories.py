# pylint: disable=redefined-outer-name
"""
Integration tests for v2/categories endpoints using real Neo4j test instance.

Tests endpoints:
- GET /v2/categories/ (get all categories)
- GET /v2/categories/{category_id} (get category by ID)
- POST /v2/categories/ (create category)
- PATCH /v2/categories/{category_id} (update category localizations)

Requires environment variables:
- NEO4J_TEST_URI: Neo4j test instance URI
- NEO4J_TEST_PASSWORD: Password for test instance
"""

import logging

import pytest
from models.category import CategoryInput
from tests.graph_assertions import snapshot_graph

logger = logging.getLogger(__name__)

APPLICATION_HEADER = {"X-Application": "test_application"}
APP_B_HEADER = {"X-Application": "app_b"}


async def _seed_app_b(test_database):
    """Seed a second application for isolation tests."""
    async with test_database.get_session() as session:
        await session.run("""
            MERGE (app:Application {id: 'app_b', name: 'Application B'})
        """)


@pytest.fixture
async def test_category_data():
    """Sample category data for testing"""
    return {
        "title": {"en": "New Test Category", "bo": "ཚོད་ལྟའི་སྡེ་ཚན་གསར་པ།"},
    }


@pytest.fixture
async def test_category_data_minimal():
    """Minimal category data for testing"""
    return {"title": {"en": "Minimal Category"}}


@pytest.mark.asyncio(loop_scope="session")
class TestGetAllCategoriesV2:
    """Tests for GET /v2/categories/ endpoint (get all categories)"""

    async def test_get_all_categories_returns_seeded_category(self, client, test_database):
        """Test getting all categories returns the seeded test category with correct fields"""
        response = await client.get("/v2/categories/", headers=APPLICATION_HEADER)

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        seeded = next((cat for cat in data if cat["id"] == "category"), None)
        assert seeded is not None
        assert seeded["title"]["en"] == "Test Category"
        assert seeded["title"]["bo"] == "ཚིག་སྒྲུབ་གསར་པ།"
        assert "children" in seeded
        assert isinstance(seeded["children"], list)

    async def test_get_all_categories_missing_application_header(self, client, test_database):
        """Test getting categories without X-Application header fails"""
        response = await client.get("/v2/categories/")

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    async def test_get_all_categories_invalid_application(self, client, test_database):
        """Test getting categories with invalid application returns 404"""
        response = await client.get("/v2/categories/", headers={"X-Application": "nonexistent_app"})

        assert response.status_code == 404
        data = response.json()
        assert "error" in data

    async def test_get_all_categories_with_parent_id_filter(self, client, test_database):
        """Test filtering categories by parent_id"""
        parent_data = {"title": {"en": "Parent Category"}}
        parent = CategoryInput.model_validate(parent_data)
        parent_id = await test_database.category.create(parent, application="test_application")

        child_data = {"title": {"en": "Child Category"}, "parent_id": parent_id}
        child = CategoryInput.model_validate(child_data)
        child_id = await test_database.category.create(child, application="test_application")

        response = await client.get(f"/v2/categories/?parent_id={parent_id}", headers=APPLICATION_HEADER)

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == child_id
        assert data[0]["parent_id"] == parent_id

    async def test_get_all_categories_root_only(self, client, test_database):
        """Test getting only root categories (no parent_id filter returns roots)"""
        parent_data = {"title": {"en": "Root Category For Test"}}
        parent = CategoryInput.model_validate(parent_data)
        parent_id = await test_database.category.create(parent, application="test_application")

        child_data = {"title": {"en": "Child Of Root"}, "parent_id": parent_id}
        child = CategoryInput.model_validate(child_data)
        await test_database.category.create(child, application="test_application")

        response = await client.get("/v2/categories/", headers=APPLICATION_HEADER)

        assert response.status_code == 200
        data = response.json()
        root_ids = [cat["id"] for cat in data if cat.get("parent_id") is None]
        assert parent_id in root_ids

    async def test_get_all_categories_children_field(self, client, test_database):
        """Test that categories include children field with child IDs"""
        parent_data = {"title": {"en": "Parent With Children"}}
        parent = CategoryInput.model_validate(parent_data)
        parent_id = await test_database.category.create(parent, application="test_application")

        child1_data = {"title": {"en": "First Child"}, "parent_id": parent_id}
        child1 = CategoryInput.model_validate(child1_data)
        child1_id = await test_database.category.create(child1, application="test_application")

        child2_data = {"title": {"en": "Second Child"}, "parent_id": parent_id}
        child2 = CategoryInput.model_validate(child2_data)
        child2_id = await test_database.category.create(child2, application="test_application")

        response = await client.get("/v2/categories/", headers=APPLICATION_HEADER)

        assert response.status_code == 200
        data = response.json()
        parent_cat = next((cat for cat in data if cat["id"] == parent_id), None)
        assert parent_cat is not None
        assert "children" in parent_cat
        assert isinstance(parent_cat["children"], list)
        assert len(parent_cat["children"]) == 2
        assert child1_id in parent_cat["children"]
        assert child2_id in parent_cat["children"]

    async def test_get_all_categories_no_children(self, client, test_database):
        """Test that categories without children have empty children list"""
        leaf_data = {"title": {"en": "Leaf Category"}}
        leaf = CategoryInput.model_validate(leaf_data)
        leaf_id = await test_database.category.create(leaf, application="test_application")

        response = await client.get("/v2/categories/", headers=APPLICATION_HEADER)

        assert response.status_code == 200
        data = response.json()
        leaf_cat = next((cat for cat in data if cat["id"] == leaf_id), None)
        assert leaf_cat is not None
        assert "children" in leaf_cat
        assert leaf_cat["children"] == []


@pytest.mark.asyncio(loop_scope="session")
class TestGetCategoryByIdV2:
    """Tests for GET /v2/categories/{category_id} endpoint"""

    async def test_get_seeded_category_by_id(self, client, test_database):
        """Test retrieving the seeded category by its known ID"""
        response = await client.get("/v2/categories/category", headers=APPLICATION_HEADER)

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "category"
        assert data["title"]["en"] == "Test Category"
        assert data["title"]["bo"] == "ཚིག་སྒྲུབ་གསར་པ།"
        assert "children" in data
        assert isinstance(data["children"], list)

    async def test_get_created_category_by_id(self, client, test_database):
        """Test retrieving a dynamically created category by ID"""
        create_response = await client.post(
            "/v2/categories/",
            json={"title": {"en": "Get By Id Test"}, "description": {"en": "A test description"}},
            headers=APPLICATION_HEADER,
        )
        assert create_response.status_code == 201
        category_id = create_response.json()["id"]

        response = await client.get(f"/v2/categories/{category_id}", headers=APPLICATION_HEADER)

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == category_id
        assert data["title"]["en"] == "Get By Id Test"
        assert data["description"]["en"] == "A test description"
        assert data["parent_id"] is None
        assert data["children"] == []

    async def test_get_category_by_id_not_found(self, client, test_database):
        """Test requesting a non-existent category returns 404"""
        response = await client.get("/v2/categories/nonexistent_id", headers=APPLICATION_HEADER)

        assert response.status_code == 404
        data = response.json()
        assert "error" in data

    async def test_get_category_by_id_missing_application_header(self, client, test_database):
        """Test requesting a category without X-Application header fails"""
        response = await client.get("/v2/categories/category")

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    async def test_get_category_by_id_invalid_application(self, client, test_database):
        """Test requesting a category with invalid application returns 404"""
        response = await client.get(
            "/v2/categories/category",
            headers={"X-Application": "nonexistent_app"},
        )

        assert response.status_code == 404
        data = response.json()
        assert "error" in data

    async def test_get_category_by_id_wrong_application(self, client, test_database):
        """Test that a category from one application is not visible to another"""
        await _seed_app_b(test_database)

        create_response = await client.post(
            "/v2/categories/",
            json={"title": {"en": "App A Only Category"}},
            headers=APPLICATION_HEADER,
        )
        assert create_response.status_code == 201
        category_id = create_response.json()["id"]

        response = await client.get(f"/v2/categories/{category_id}", headers=APP_B_HEADER)
        assert response.status_code == 404

    async def test_get_category_by_id_with_children(self, client, test_database):
        """Test that retrieved category includes children IDs"""
        parent_data = {"title": {"en": "Parent For GetById"}}
        parent = CategoryInput.model_validate(parent_data)
        parent_id = await test_database.category.create(parent, application="test_application")

        child_data = {"title": {"en": "Child For GetById"}, "parent_id": parent_id}
        child = CategoryInput.model_validate(child_data)
        child_id = await test_database.category.create(child, application="test_application")

        response = await client.get(f"/v2/categories/{parent_id}", headers=APPLICATION_HEADER)

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == parent_id
        assert child_id in data["children"]

    async def test_get_category_by_id_with_parent(self, client, test_database):
        """Test that a child category includes parent_id"""
        parent_data = {"title": {"en": "Parent For Child GetById"}}
        parent = CategoryInput.model_validate(parent_data)
        parent_id = await test_database.category.create(parent, application="test_application")

        child_data = {"title": {"en": "Child With Parent GetById"}, "parent_id": parent_id}
        child = CategoryInput.model_validate(child_data)
        child_id = await test_database.category.create(child, application="test_application")

        response = await client.get(f"/v2/categories/{child_id}", headers=APPLICATION_HEADER)

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == child_id
        assert data["parent_id"] == parent_id


@pytest.mark.asyncio(loop_scope="session")
class TestCreateCategoryV2:
    """Tests for POST /v2/categories/ endpoint (create category)"""

    async def test_create_category_success(self, client, test_database, test_category_data):
        """Test successfully creating a category and verifying it appears in GET"""
        response = await client.post(
            "/v2/categories/",
            json=test_category_data,
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["id"] is not None

        get_response = await client.get("/v2/categories/", headers=APPLICATION_HEADER)
        categories = get_response.json()
        created_cat = next((c for c in categories if c["id"] == data["id"]), None)
        assert created_cat is not None
        assert created_cat["title"]["en"] == "New Test Category"
        assert created_cat["title"]["bo"] == "ཚོད་ལྟའི་སྡེ་ཚན་གསར་པ།"
        assert created_cat.get("parent_id") is None
        assert created_cat["children"] == []

    async def test_create_category_minimal(self, client, test_database, test_category_data_minimal):
        """Test creating category with minimal data"""
        response = await client.post(
            "/v2/categories/",
            json=test_category_data_minimal,
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 201
        data = response.json()
        assert "id" in data

        stored = (await client.get(f"/v2/categories/{data['id']}", headers=APPLICATION_HEADER)).json()
        assert stored["title"] == test_category_data_minimal["title"]
        assert stored.get("description") is None
        assert stored.get("parent_id") is None

    async def test_create_category_with_parent(self, client, test_database):
        """Test creating a child category with parent_id"""
        parent_data = {"title": {"en": "Parent For Create Test"}}
        parent = CategoryInput.model_validate(parent_data)
        parent_id = await test_database.category.create(parent, application="test_application")

        child_data = {"title": {"en": "Child Category"}, "parent_id": parent_id}

        response = await client.post(
            "/v2/categories/",
            json=child_data,
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 201
        data = response.json()
        child_id = data["id"]

        get_response = await client.get("/v2/categories/", headers=APPLICATION_HEADER)
        categories = get_response.json()
        parent_cat = next((cat for cat in categories if cat["id"] == parent_id), None)
        assert parent_cat is not None
        assert child_id in parent_cat["children"]

    async def test_create_category_missing_application_header(self, client, test_database):
        """Test creating category without X-Application header fails"""
        category_data = {"title": {"en": "No App Header"}}

        response = await client.post("/v2/categories/", json=category_data)

        assert response.status_code == 422

    async def test_create_category_invalid_application(self, client, test_database):
        """Test creating category with invalid application returns 404"""
        category_data = {"title": {"en": "Invalid App"}}

        response = await client.post(
            "/v2/categories/",
            json=category_data,
            headers={"X-Application": "nonexistent_app"},
        )

        assert response.status_code == 404
        data = response.json()
        assert "error" in data

    async def test_create_category_missing_title(self, client, test_database):
        """Test creating category without title fails"""
        response = await client.post(
            "/v2/categories/",
            json={},
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    async def test_create_category_empty_title(self, client, test_database):
        """Test creating category with empty title fails"""
        response = await client.post(
            "/v2/categories/",
            json={"title": {}},
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    async def test_create_category_nonexistent_parent_id(self, client, test_database):
        """Test creating category with nonexistent parent_id fails"""
        category_data = {"title": {"en": "Orphan Child"}, "parent_id": "nonexistent_parent"}

        response = await client.post(
            "/v2/categories/",
            json=category_data,
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 404
        data = response.json()
        assert "error" in data

    async def test_create_category_rejects_parent_from_other_application(self, client, test_database):
        """Test child categories cannot cross application boundaries."""
        await _seed_app_b(test_database)
        parent = CategoryInput.model_validate({"title": {"en": "Cross App Parent"}})
        parent_id = await test_database.category.create(parent, application="test_application")

        response = await client.post(
            "/v2/categories/",
            json={"title": {"en": "Cross App Child"}, "parent_id": parent_id},
            headers=APP_B_HEADER,
        )

        assert response.status_code == 404
        data = response.json()
        assert "error" in data

    async def test_create_category_duplicate_title_rejected(self, client, test_database):
        """Test creating category with duplicate title in same parent fails"""
        category_data = {"title": {"en": "Unique Title For Duplicate Test"}}

        response1 = await client.post(
            "/v2/categories/",
            json=category_data,
            headers=APPLICATION_HEADER,
        )
        assert response1.status_code == 201

        response2 = await client.post(
            "/v2/categories/",
            json=category_data,
            headers=APPLICATION_HEADER,
        )
        assert response2.status_code == 422
        data = response2.json()
        assert "error" in data
        assert "already exists" in data["error"].lower()


@pytest.mark.asyncio(loop_scope="session")
class TestUpdateCategoryV2:
    """Tests for PATCH /v2/categories/{category_id} endpoint (update category localizations)"""

    async def test_patch_category_adds_title_language(self, client, test_database):
        """Test adding a title language keeps the existing languages"""
        category = CategoryInput.model_validate({"title": {"en": "Patch Add Title", "bo": "ཁ་སྣོན་མཚན་བྱང་།"}})
        category_id = await test_database.category.create(category, application="test_application")

        response = await client.patch(
            f"/v2/categories/{category_id}",
            json={"title": {"zh": "添加标题"}},
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == category_id
        assert data["title"] == {"en": "Patch Add Title", "bo": "ཁ་སྣོན་མཚན་བྱང་།", "zh": "添加标题"}

        get_response = await client.get(f"/v2/categories/{category_id}", headers=APPLICATION_HEADER)
        assert get_response.status_code == 200
        assert get_response.json()["title"] == {"en": "Patch Add Title", "bo": "ཁ་སྣོན་མཚན་བྱང་།", "zh": "添加标题"}

    async def test_patch_category_overwrites_title_language(self, client, test_database):
        """Test overwriting a title language keeps the other languages"""
        category = CategoryInput.model_validate({"title": {"en": "Patch Old Title", "bo": "མཚན་བྱང་རྙིང་པ།"}})
        category_id = await test_database.category.create(category, application="test_application")

        response = await client.patch(
            f"/v2/categories/{category_id}",
            json={"title": {"en": "Patch New Title"}},
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 200
        assert response.json()["title"] == {"en": "Patch New Title", "bo": "མཚན་བྱང་རྙིང་པ།"}

    async def test_patch_category_adds_and_overwrites_in_one_request(self, client, test_database):
        """Test adding and overwriting languages in the same request"""
        category = CategoryInput.model_validate({"title": {"en": "Patch Mixed Title", "bo": "མཚན་བྱང་།"}})
        category_id = await test_database.category.create(category, application="test_application")

        response = await client.patch(
            f"/v2/categories/{category_id}",
            json={"title": {"en": "Patch Mixed Title Updated", "zh": "混合标题"}},
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 200
        assert response.json()["title"] == {"en": "Patch Mixed Title Updated", "bo": "མཚན་བྱང་།", "zh": "混合标题"}

    async def test_patch_category_title_language_matched_case_insensitively(self, client, test_database):
        """Test a differently-cased language code replaces the existing title instead of adding a second one"""
        category = CategoryInput.model_validate({"title": {"en-US": "Patch Case Old", "bo": "ཡིག་འབྲུ།"}})
        category_id = await test_database.category.create(category, application="test_application")

        response = await client.patch(
            f"/v2/categories/{category_id}",
            json={"title": {"en-us": "Patch Case New"}},
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 200
        assert response.json()["title"] == {"en-us": "Patch Case New", "bo": "ཡིག་འབྲུ།"}

        get_response = await client.get(f"/v2/categories/{category_id}", headers=APPLICATION_HEADER)
        assert get_response.json()["title"] == {"en-us": "Patch Case New", "bo": "ཡིག་འབྲུ།"}

    async def test_patch_category_description_language_matched_case_insensitively(self, client, test_database):
        """Test a differently-cased language code replaces the existing description"""
        category = CategoryInput.model_validate(
            {"title": {"en": "Patch Case Description"}, "description": {"EN": "Old description", "bo": "རྙིང་པ།"}}
        )
        category_id = await test_database.category.create(category, application="test_application")

        response = await client.patch(
            f"/v2/categories/{category_id}",
            json={"description": {"en": "New description"}},
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 200
        assert response.json()["description"] == {"en": "New description", "bo": "རྙིང་པ།"}

    async def test_patch_category_adds_description_when_missing(self, client, test_database):
        """Test adding a description to a category that has none"""
        category = CategoryInput.model_validate({"title": {"en": "Patch No Description"}})
        category_id = await test_database.category.create(category, application="test_application")

        response = await client.patch(
            f"/v2/categories/{category_id}",
            json={"description": {"zh": "新的描述"}},
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["description"] == {"zh": "新的描述"}
        assert data["title"] == {"en": "Patch No Description"}

    async def test_patch_category_merges_description(self, client, test_database):
        """Test description languages are added or replaced and the rest are kept"""
        category = CategoryInput.model_validate(
            {
                "title": {"en": "Patch Description Merge"},
                "description": {"en": "Old description", "bo": "འགྲེལ་བཤད་རྙིང་པ།"},
            }
        )
        category_id = await test_database.category.create(category, application="test_application")

        response = await client.patch(
            f"/v2/categories/{category_id}",
            json={"description": {"en": "New description", "zh": "描述"}},
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 200
        assert response.json()["description"] == {
            "en": "New description",
            "bo": "འགྲེལ་བཤད་རྙིང་པ།",
            "zh": "描述",
        }

    async def test_patch_category_title_only_keeps_description(self, client, test_database):
        """Test patching only the title leaves the description unchanged"""
        category = CategoryInput.model_validate(
            {"title": {"en": "Patch Title Only"}, "description": {"en": "Untouched description"}}
        )
        category_id = await test_database.category.create(category, application="test_application")

        response = await client.patch(
            f"/v2/categories/{category_id}",
            json={"title": {"zh": "仅标题"}},
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 200
        assert response.json()["description"] == {"en": "Untouched description"}

    async def test_patch_category_keeps_parent_and_children(self, client, test_database):
        """Test patching localizations does not change the hierarchy"""
        parent = CategoryInput.model_validate({"title": {"en": "Patch Hierarchy Parent"}})
        parent_id = await test_database.category.create(parent, application="test_application")
        child = CategoryInput.model_validate({"title": {"en": "Patch Hierarchy Child"}, "parent_id": parent_id})
        child_id = await test_database.category.create(child, application="test_application")
        grandchild = CategoryInput.model_validate(
            {"title": {"en": "Patch Hierarchy Grandchild"}, "parent_id": child_id}
        )
        grandchild_id = await test_database.category.create(grandchild, application="test_application")

        response = await client.patch(
            f"/v2/categories/{child_id}",
            json={"title": {"zh": "子类"}, "description": {"en": "Child description"}},
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["parent_id"] == parent_id
        assert data["children"] == [grandchild_id]

    async def test_patch_category_with_own_title_allowed(self, client, test_database):
        """Test resending the category's own title is not treated as a duplicate"""
        category = CategoryInput.model_validate({"title": {"en": "Patch Own Title"}})
        category_id = await test_database.category.create(category, application="test_application")

        response = await client.patch(
            f"/v2/categories/{category_id}",
            json={"title": {"en": "Patch Own Title"}},
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 200
        assert response.json()["title"] == {"en": "Patch Own Title"}

    async def test_patch_category_duplicate_sibling_title_rejected(self, client, test_database):
        """Test a title that duplicates a sibling category in the same language is rejected"""
        sibling = CategoryInput.model_validate({"title": {"zh": "重复标题", "en": "Patch Sibling"}})
        await test_database.category.create(sibling, application="test_application")
        category = CategoryInput.model_validate({"title": {"en": "Patch Duplicate Target"}})
        category_id = await test_database.category.create(category, application="test_application")

        response = await client.patch(
            f"/v2/categories/{category_id}",
            json={"title": {"zh": "重复标题"}},
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 422
        data = response.json()
        assert "already exists" in data["error"].lower()

        get_response = await client.get(f"/v2/categories/{category_id}", headers=APPLICATION_HEADER)
        assert get_response.json()["title"] == {"en": "Patch Duplicate Target"}

    async def test_patch_category_same_title_under_different_parent_allowed(self, client, test_database):
        """Test a title used by a category under another parent is allowed"""
        other = CategoryInput.model_validate({"title": {"en": "Patch Other Parent"}})
        other_id = await test_database.category.create(other, application="test_application")
        cousin = CategoryInput.model_validate({"title": {"zh": "表亲标题"}, "parent_id": other_id})
        await test_database.category.create(cousin, application="test_application")
        category = CategoryInput.model_validate({"title": {"en": "Patch Root Without Cousin"}})
        category_id = await test_database.category.create(category, application="test_application")

        response = await client.patch(
            f"/v2/categories/{category_id}",
            json={"title": {"zh": "表亲标题"}},
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 200

    async def test_patch_category_not_found(self, client, test_database):
        """Test patching a nonexistent category returns 404"""
        response = await client.patch(
            "/v2/categories/nonexistent_category_id",
            json={"title": {"zh": "不存在"}},
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 404
        data = response.json()
        assert "error" in data

    async def test_patch_category_from_other_application_not_found(self, client, test_database):
        """Test a category cannot be patched through another application"""
        await _seed_app_b(test_database)
        category = CategoryInput.model_validate({"title": {"en": "Patch Cross App"}})
        category_id = await test_database.category.create(category, application="test_application")

        response = await client.patch(
            f"/v2/categories/{category_id}",
            json={"title": {"zh": "跨应用"}},
            headers=APP_B_HEADER,
        )

        assert response.status_code == 404

        get_response = await client.get(f"/v2/categories/{category_id}", headers=APPLICATION_HEADER)
        assert get_response.json()["title"] == {"en": "Patch Cross App"}

    async def test_patch_category_invalid_application(self, client, test_database):
        """Test patching with an invalid application returns 404"""
        response = await client.patch(
            "/v2/categories/category",
            json={"title": {"zh": "无效应用"}},
            headers={"X-Application": "nonexistent_app"},
        )

        assert response.status_code == 404
        data = response.json()
        assert "error" in data

    async def test_patch_category_missing_application_header(self, client, test_database):
        """Test patching without X-Application header fails"""
        response = await client.patch("/v2/categories/category", json={"title": {"zh": "无标头"}})

        assert response.status_code == 422

    async def test_patch_category_invalid_language_code(self, client, test_database):
        """Test patching with a language code not in /v2/languages returns 400"""
        category = CategoryInput.model_validate({"title": {"en": "Patch Invalid Language"}})
        category_id = await test_database.category.create(category, application="test_application")

        response = await client.patch(
            f"/v2/categories/{category_id}",
            json={"title": {"xx": "Invalid Language"}},
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 400
        data = response.json()
        assert "error" in data

    async def test_patch_category_invalid_description_language_rolls_back_title(self, client, test_database):
        """Test a failing description leaves the graph untouched, including the title"""
        category = CategoryInput.model_validate(
            {"title": {"en": "Patch Rollback"}, "description": {"en": "Rollback description"}}
        )
        category_id = await test_database.category.create(category, application="test_application")
        before = await snapshot_graph(test_database)

        response = await client.patch(
            f"/v2/categories/{category_id}",
            json={"title": {"zh": "回滚"}, "description": {"xx": "Invalid Language"}},
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 400
        assert await snapshot_graph(test_database) == before

    async def test_patch_category_replaces_nomens_without_orphans(self, client, test_database):
        """Test the old title and description nomens are replaced, not left orphaned"""
        category = CategoryInput.model_validate(
            {"title": {"en": "Patch Orphan Check"}, "description": {"en": "Orphan description"}}
        )
        category_id = await test_database.category.create(category, application="test_application")

        count_query = """
            MATCH (c:Category {id: $category_id})
            RETURN count { (c)-[:HAS_TITLE]->(:Nomen) } AS title_count,
                   count { (c)-[:HAS_DESCRIPTION]->(:Nomen) } AS description_count,
                   count { MATCH (n:Nomen) } AS nomen_count,
                   count { MATCH (lt:LocalizedText) } AS localized_text_count
        """
        async with test_database.get_session() as session:
            before = await (await session.run(count_query, category_id=category_id)).single()

        response = await client.patch(
            f"/v2/categories/{category_id}",
            json={"title": {"zh": "孤儿检查"}, "description": {"zh": "孤儿描述"}},
            headers=APPLICATION_HEADER,
        )
        assert response.status_code == 200

        async with test_database.get_session() as session:
            after = await (await session.run(count_query, category_id=category_id)).single()

        assert after["title_count"] == 1
        assert after["description_count"] == 1
        assert after["nomen_count"] == before["nomen_count"]
        assert after["localized_text_count"] == before["localized_text_count"] + 2

    async def test_patch_category_empty_body_rejected(self, client, test_database):
        """Test patching with no fields returns 422"""
        response = await client.patch("/v2/categories/category", json={}, headers=APPLICATION_HEADER)

        assert response.status_code == 422

    async def test_patch_category_null_field_rejected(self, client, test_database):
        """Test null fields are rejected instead of clearing localizations"""
        response = await client.patch(
            "/v2/categories/category",
            json={"description": None},
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 422

    async def test_patch_category_empty_localization_rejected(self, client, test_database):
        """Test an empty title mapping returns 422"""
        response = await client.patch("/v2/categories/category", json={"title": {}}, headers=APPLICATION_HEADER)

        assert response.status_code == 422

    async def test_patch_category_rejects_parent_id(self, client, test_database):
        """Test parent_id is not accepted by the localization endpoint"""
        response = await client.patch(
            "/v2/categories/category",
            json={"parent_id": "category"},
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 422


@pytest.mark.asyncio(loop_scope="session")
class TestCategoryHierarchy:
    """Tests for category parent-child hierarchy"""

    async def test_three_level_hierarchy(self, client, test_database):
        """Test creating a three-level category hierarchy"""
        grandparent_data = {"title": {"en": "Grandparent"}}
        grandparent = CategoryInput.model_validate(grandparent_data)
        grandparent_id = await test_database.category.create(grandparent, application="test_application")

        parent_data = {"title": {"en": "Parent"}, "parent_id": grandparent_id}
        parent = CategoryInput.model_validate(parent_data)
        parent_id = await test_database.category.create(parent, application="test_application")

        child_data = {"title": {"en": "Child"}, "parent_id": parent_id}
        child = CategoryInput.model_validate(child_data)
        child_id = await test_database.category.create(child, application="test_application")

        response = await client.get("/v2/categories/", headers=APPLICATION_HEADER)
        categories = response.json()

        grandparent_cat = next((c for c in categories if c["id"] == grandparent_id), None)
        assert grandparent_cat is not None
        assert grandparent_cat["parent_id"] is None
        assert parent_id in grandparent_cat["children"]

        response_children = await client.get(f"/v2/categories/?parent_id={grandparent_id}", headers=APPLICATION_HEADER)
        children_of_grandparent = response_children.json()
        parent_cat = next((c for c in children_of_grandparent if c["id"] == parent_id), None)
        assert parent_cat is not None
        assert parent_cat["parent_id"] == grandparent_id
        assert child_id in parent_cat["children"]

    async def test_multiple_children_same_parent(self, client, test_database):
        """Test that a parent can have multiple children"""
        parent_data = {"title": {"en": "Multi Child Parent"}}
        parent = CategoryInput.model_validate(parent_data)
        parent_id = await test_database.category.create(parent, application="test_application")

        child_ids = []
        for i in range(5):
            child_data = {"title": {"en": f"Child {i}"}, "parent_id": parent_id}
            child = CategoryInput.model_validate(child_data)
            child_id = await test_database.category.create(child, application="test_application")
            child_ids.append(child_id)

        response = await client.get("/v2/categories/", headers=APPLICATION_HEADER)
        categories = response.json()

        parent_cat = next((c for c in categories if c["id"] == parent_id), None)
        assert parent_cat is not None
        assert len(parent_cat["children"]) == 5
        for child_id in child_ids:
            assert child_id in parent_cat["children"]

    async def test_sibling_categories_independent(self, client, test_database):
        """Test that sibling categories don't affect each other's children"""
        parent_data = {"title": {"en": "Sibling Test Parent"}}
        parent = CategoryInput.model_validate(parent_data)
        parent_id = await test_database.category.create(parent, application="test_application")

        sibling1_data = {"title": {"en": "Sibling 1"}, "parent_id": parent_id}
        sibling1 = CategoryInput.model_validate(sibling1_data)
        sibling1_id = await test_database.category.create(sibling1, application="test_application")

        sibling2_data = {"title": {"en": "Sibling 2"}, "parent_id": parent_id}
        sibling2 = CategoryInput.model_validate(sibling2_data)
        sibling2_id = await test_database.category.create(sibling2, application="test_application")

        child_of_sibling1_data = {"title": {"en": "Child of Sibling 1"}, "parent_id": sibling1_id}
        child_of_sibling1 = CategoryInput.model_validate(child_of_sibling1_data)
        child_of_sibling1_id = await test_database.category.create(child_of_sibling1, application="test_application")

        response = await client.get(f"/v2/categories/?parent_id={parent_id}", headers=APPLICATION_HEADER)
        siblings = response.json()

        sibling1_cat = next((c for c in siblings if c["id"] == sibling1_id), None)
        sibling2_cat = next((c for c in siblings if c["id"] == sibling2_id), None)

        assert sibling1_cat is not None
        assert sibling2_cat is not None
        assert child_of_sibling1_id in sibling1_cat["children"]
        assert sibling2_cat["children"] == []

    async def test_category_title_localization(self, client, test_database):
        """Test that category titles are properly localized"""
        category_data = {
            "title": {
                "en": "English Title",
                "bo": "བོད་ཡིག་མིང་།",
                "zh": "中文标题",
            }
        }

        response = await client.post(
            "/v2/categories/",
            json=category_data,
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 201
        category_id = response.json()["id"]

        get_response = await client.get("/v2/categories/", headers=APPLICATION_HEADER)
        categories = get_response.json()

        created_cat = next((c for c in categories if c["id"] == category_id), None)
        assert created_cat is not None
        assert created_cat["title"]["en"] == "English Title"
        assert created_cat["title"]["bo"] == "བོད་ཡིག་མིང་།"
        assert created_cat["title"]["zh"] == "中文标题"


@pytest.mark.asyncio(loop_scope="session")
class TestCategoryDescription:
    """Tests for category description field (optional, localized via Nomen)"""

    async def test_create_category_with_description_single_language(self, client, test_database):
        """Test creating a category with description in one language"""
        category_data = {
            "title": {"en": "Category With Description"},
            "description": {"en": "will add description here"},
        }

        response = await client.post(
            "/v2/categories/",
            json=category_data,
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 201
        data = response.json()
        category_id = data["id"]

        get_response = await client.get("/v2/categories/", headers=APPLICATION_HEADER)
        categories = get_response.json()
        created_cat = next((c for c in categories if c["id"] == category_id), None)

        assert created_cat is not None
        assert created_cat["description"] is not None
        assert created_cat["description"]["en"] == "will add description here"

    async def test_create_category_with_description_multiple_languages(self, client, test_database):
        """Test creating a category with description in multiple languages"""
        category_data = {
            "title": {"en": "Multi-Lang Desc Category"},
            "description": {
                "en": "A category for texts",
                "bo": "གཞུང་དེབ་སྡེ་ཚན།",
                "zh": "文本分类",
            },
        }

        response = await client.post(
            "/v2/categories/",
            json=category_data,
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 201
        category_id = response.json()["id"]

        get_response = await client.get("/v2/categories/", headers=APPLICATION_HEADER)
        categories = get_response.json()
        created_cat = next((c for c in categories if c["id"] == category_id), None)

        assert created_cat is not None
        assert created_cat["description"]["en"] == "A category for texts"
        assert created_cat["description"]["bo"] == "གཞུང་དེབ་སྡེ་ཚན།"
        assert created_cat["description"]["zh"] == "文本分类"

    async def test_create_category_without_description(self, client, test_database):
        """Test creating a category without description (backward compatibility)"""
        category_data = {"title": {"en": "Category Without Description"}}

        response = await client.post(
            "/v2/categories/",
            json=category_data,
            headers=APPLICATION_HEADER,
        )

        assert response.status_code == 201
        category_id = response.json()["id"]

        get_response = await client.get("/v2/categories/", headers=APPLICATION_HEADER)
        categories = get_response.json()
        created_cat = next((c for c in categories if c["id"] == category_id), None)

        assert created_cat is not None
        assert created_cat.get("description") is None

    async def test_get_categories_returns_description_when_present(self, client, test_database):
        """Test that GET returns description when category has one"""
        category_with_desc = CategoryInput.model_validate({
            "title": {"en": "Desc Via DB"},
            "description": {"en": "Created via database with description"},
        })
        category_id = await test_database.category.create(
            category_with_desc, application="test_application"
        )

        response = await client.get("/v2/categories/", headers=APPLICATION_HEADER)
        categories = response.json()
        created_cat = next((c for c in categories if c["id"] == category_id), None)

        assert created_cat is not None
        assert created_cat["description"]["en"] == "Created via database with description"

    async def test_get_categories_seeded_category_no_description(self, client, test_database):
        """Test that seeded category (no HAS_DESCRIPTION) returns description null/absent"""
        response = await client.get("/v2/categories/", headers=APPLICATION_HEADER)

        assert response.status_code == 200
        categories = response.json()
        seeded_cat = next((c for c in categories if c["id"] == "category"), None)

        assert seeded_cat is not None
        assert seeded_cat.get("description") is None


@pytest.mark.asyncio(loop_scope="session")
class TestCategoryApplicationIsolation:
    """Tests that categories are isolated per application."""

    async def test_listing_categories_only_returns_own_application(self, client, test_database):
        """Categories created for App A should not appear when listing for App B."""
        await _seed_app_b(test_database)

        cat_data = {"title": {"en": "App A Exclusive Category"}}
        response_create = await client.post(
            "/v2/categories/",
            json=cat_data,
            headers=APPLICATION_HEADER,
        )
        assert response_create.status_code == 201
        cat_id_a = response_create.json()["id"]

        response_b = await client.get("/v2/categories/", headers=APP_B_HEADER)
        assert response_b.status_code == 200
        cats_b = response_b.json()
        cat_ids_b = [c["id"] for c in cats_b]
        assert cat_id_a not in cat_ids_b

    async def test_listing_categories_returns_own_categories(self, client, test_database):
        """Categories created for App B should appear for App B, not App A."""
        await _seed_app_b(test_database)

        cat_b_data = {"title": {"en": "App B Exclusive Category"}}
        response_create = await client.post(
            "/v2/categories/",
            json=cat_b_data,
            headers=APP_B_HEADER,
        )
        assert response_create.status_code == 201
        cat_id_b = response_create.json()["id"]

        response_b = await client.get("/v2/categories/", headers=APP_B_HEADER)
        cats_b = response_b.json()
        cat_ids_b = [c["id"] for c in cats_b]
        assert cat_id_b in cat_ids_b

        response_a = await client.get("/v2/categories/", headers=APPLICATION_HEADER)
        cats_a = response_a.json()
        cat_ids_a = [c["id"] for c in cats_a]
        assert cat_id_b not in cat_ids_a

    async def test_same_category_title_allowed_across_applications(self, client, test_database):
        """Two applications can each have a category with the same title."""
        await _seed_app_b(test_database)

        cat_data = {"title": {"en": "Cross App Same Category Title"}}

        response_a = await client.post(
            "/v2/categories/",
            json=cat_data,
            headers=APPLICATION_HEADER,
        )
        assert response_a.status_code == 201
        cat_id_a = response_a.json()["id"]

        response_b = await client.post(
            "/v2/categories/",
            json=cat_data,
            headers=APP_B_HEADER,
        )
        assert response_b.status_code == 201
        cat_id_b = response_b.json()["id"]

        assert cat_id_a != cat_id_b

    async def test_seeded_category_not_visible_to_app_b(self, client, test_database):
        """The seeded category (belongs to test_application) should not be visible to App B."""
        await _seed_app_b(test_database)

        response_b = await client.get("/v2/categories/", headers=APP_B_HEADER)
        assert response_b.status_code == 200
        cats_b = response_b.json()
        cat_ids_b = [c["id"] for c in cats_b]
        assert "category" not in cat_ids_b
