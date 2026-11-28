import subprocess
import sys
from http import HTTPStatus
from pathlib import Path

import httpx
import pytest

from http_errors import _map_neo4j_client_error
from main import create_app


def test_domain_exceptions_import_without_site_packages():
    subprocess.run([sys.executable, "-ES", "-c", "import errors"], cwd=Path(__file__).resolve().parents[1], check=True)


@pytest.mark.parametrize(("code", "message", "expected"), [
    ("Transaction.TransactionHookFailed", "java.lang.RuntimeException: enforce_edition_edition_of_per_text_when_1_max_one: old message. IDs: t1",
     (422, "Critical edition already present for this text")),
    ("Schema.ConstraintValidationFailed", "Error executing triggers {enforce_application_properties=java.lang.RuntimeException: enforce_application_name_property: old message. IDs: a1}",
     (422, "Invalid Application.name")),
    ("Transaction.TransactionHookFailed", "java.lang.RuntimeException: unknown error, enforce_next=second",
     (422, "Database validation failed due to trigger constraints.")),
    ("Transaction.TransactionHookFailed", "unrecognized", (422, "Database validation failed due to trigger constraints.")),
    ("Schema.ConstraintValidationFailed", "duplicate", (409, "Database constraint violation.")),
    ("Statement.SyntaxError", "bad query", (400, "bad query")),
])
def test_neo4j_error_status_and_detail(code, message, expected):
    assert _map_neo4j_client_error(f"Neo.ClientError.{code}", message) == expected


@pytest.mark.asyncio(loop_scope="session")
async def test_trigger_failure_returns_default_field_message(test_database):
    app = create_app()

    @app.post("/fail-constraint")
    async def fail_constraint():
        await test_database.query("CREATE (:Application {id: 'internal-id'})", write=True)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/fail-constraint")

    assert response.status_code == 422
    assert response.json() == {
        "type": "about:blank",
        "title": HTTPStatus(422).phrase,
        "status": 422,
        "detail": "Invalid Application.name",
    }


async def test_method_not_allowed_preserves_problem_details_and_allow_header():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.post("/__/health")

    assert response.status_code == 405
    assert response.headers["allow"] == "GET"
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "about:blank",
        "title": "Method Not Allowed",
        "status": 405,
        "detail": "Method Not Allowed",
    }
