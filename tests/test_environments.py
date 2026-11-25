"""Per-environment settings: release version, /api prefix, and the testing index guard."""

import httpx
import pytest

import main
from config import Settings, check_environment, settings


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.parametrize(
    ("environment", "content", "catalog"),
    [
        ("testing", "testing-content-search", "testing-catalog-search"),
        ("dev", "content-search", "catalog-search"),
        ("prod", "content-search", "catalog-search"),
        ("", "content-search", "catalog-search"),
    ],
)
def test_allowed_environment_settings(environment, content, catalog):
    check_environment(
        Settings(environment=environment, opensearch_index=content, opensearch_catalog_index=catalog)
    )


@pytest.mark.parametrize(
    ("content", "catalog", "named"),
    [
        ("content-search", "testing-catalog-search", "content-search"),
        ("testing-content-search", "catalog-search", "catalog-search"),
        ("content-search", "catalog-search", "content-search, catalog-search"),
    ],
)
def test_testing_refuses_prod_index_names(content, catalog, named):
    config = Settings(environment="testing", opensearch_index=content, opensearch_catalog_index=catalog)
    with pytest.raises(RuntimeError, match=named):
        check_environment(config)


async def test_lifespan_refuses_testing_with_prod_indexes(monkeypatch):
    monkeypatch.setattr(
        main,
        "settings",
        settings.model_copy(update={"environment": "testing", "neo4j_uri": "bolt://unused"}),
    )
    app = main.create_app()
    with pytest.raises(RuntimeError, match="testing-"):
        async with main.lifespan(app):
            pass


async def test_health_and_swagger_show_release_version(monkeypatch):
    monkeypatch.setattr(main, "settings", settings.model_copy(update={"app_version": "2.16.0"}))
    app = main.create_app()
    async with _client(app) as client:
        assert (await client.get("/__/health")).json() == {"status": "healthy", "version": "2.16.0"}
        assert (await client.get("/openapi.json")).json()["info"]["version"] == "2.16.0"


async def test_version_falls_back_to_api_contract_version(monkeypatch):
    monkeypatch.setattr(main, "settings", settings.model_copy(update={"app_version": ""}))
    app = main.create_app()
    async with _client(app) as client:
        assert (await client.get("/__/health")).json()["version"] == main.API_VERSION


async def test_api_prefix_and_old_root_paths_both_work(monkeypatch):
    monkeypatch.setattr(main, "settings", settings.model_copy(update={"root_path": "/api"}))
    app = main.create_app()
    async with _client(app) as client:
        # nginx forwards /api/... unchanged; old callers still use the bare paths.
        for path in ("/api/__/health", "/__/health"):
            assert (await client.get(path)).json()["status"] == "healthy", path
        schema = (await client.get("/api/openapi.json")).json()
        assert schema["servers"] == [{"url": "/api"}]
        assert "/v2/texts" in schema["paths"]
        docs = await client.get("/api/docs")
        assert docs.status_code == 200
        assert "/api/openapi.json" in docs.text
