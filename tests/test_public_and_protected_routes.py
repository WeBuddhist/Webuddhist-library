"""Route wiring checks: authentication must run before protected handlers."""

import re

import httpx
import pytest

from main import create_app
from config import settings

# From the OpenAPI schema: FastAPI keeps included routers as one entry each in app.routes,
# so filtering app.routes for APIRoute finds none of the /v2 routes.
ROUTES = [
    (method.upper(), path)
    for path, operations in create_app().openapi()["paths"].items()
    if path.startswith("/v2/")
    for method in sorted(operations)
]
assert ROUTES, "no /v2 routes found; the check below would pass without testing anything"


@pytest.mark.parametrize("method,path", ROUTES)
async def test_protected_route_rejects_missing_key(monkeypatch, method, path):
    app = create_app(testing=False)
    # Dev is public too, so it checks keys like testing and prod.
    monkeypatch.setattr(settings, "environment", "dev")
    # These sentinels also catch accidental entry into a handler before authentication.
    app.state.db = app.state.storage = None
    app.state.content_search = app.state.catalog_search = None
    url = re.sub(r"\{[^}]+\}", "missing", path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, url, json={})
    assert response.status_code == 401, (method, path, response.text)
    assert response.json()["error"] == "Missing required header: X-API-Key"


async def test_health_schema_and_documentation_are_public(monkeypatch):
    app = create_app(testing=False)
    monkeypatch.setattr(settings, "environment", "test")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/__/health")).json()["status"] == "healthy"
        schema = await client.get("/openapi.json")
        assert schema.status_code == 200
        assert {"/v2/texts", "/v2/persons", "/v2/content-search"} <= schema.json()["paths"].keys()
        for path in ("/docs", "/redoc"):
            response = await client.get(path)
            assert response.status_code == 200
            assert "text/html" in response.headers["content-type"]
