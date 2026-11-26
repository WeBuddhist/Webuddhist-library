import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from http_errors import _map_neo4j_client_error
from main import create_app


def test_domain_exceptions_import_without_site_packages():
    subprocess.run([sys.executable, "-ES", "-c", "import errors"], cwd=Path(__file__).resolve().parents[1], check=True)


@pytest.mark.parametrize(("code", "message", "expected"), [
    ("Transaction.TransactionHookFailed", "java.lang.RuntimeException: first, enforce_next=second", (422, "first")),
    ("Schema.ConstraintValidationFailed", "Error executing triggers {first=reason, next=other}", (422, "first: reason")),
    ("Transaction.TransactionHookFailed", "unrecognized", (422, "Database validation failed due to trigger constraints.")),
    ("Schema.ConstraintValidationFailed", "duplicate", (409, "Database constraint violation.")),
    ("Statement.SyntaxError", "bad query", (400, "bad query")),
])
def test_neo4j_error_status_and_detail(code, message, expected):
    assert _map_neo4j_client_error(f"Neo.ClientError.{code}", message) == expected


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
