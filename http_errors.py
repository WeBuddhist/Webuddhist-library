"""HTTP exception handlers and RFC 9457 problem details for the API."""

import logging
import re
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from neo4j.exceptions import ClientError as Neo4jClientError
from opensearchpy.exceptions import OpenSearchException
from pydantic import BaseModel, ValidationError
from starlette.exceptions import HTTPException

from database.neo4j_triggers import RULE_DESCRIPTIONS
from errors import OpenPechaError

logger = logging.getLogger("errors")

PROBLEM_JSON = "application/problem+json"


class Problem(BaseModel):
    """RFC 9457 problem details; with `type` about:blank, `title` is the HTTP status phrase."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str
    errors: list[dict[str, str]] | None = None


def problem(status: int, detail: str, errors: list[dict[str, str]] | None = None) -> JSONResponse:
    body = Problem(title=HTTPStatus(status).phrase, status=status, detail=detail, errors=errors)
    return JSONResponse(body.model_dump(exclude_none=True), status, media_type=PROBLEM_JSON)


def _map_neo4j_client_error(code: str, message: str) -> tuple[int, str]:
    if code == "Neo.ClientError.Transaction.TransactionHookFailed" or "Error executing triggers" in message:
        for name in re.findall(r"\b(enforce_[a-z0-9_]+):", message):
            if description := RULE_DESCRIPTIONS.get(name):
                return 422, description
        return 422, "Database validation failed due to trigger constraints."
    if "Constraint" in code:
        return 409, "Database constraint violation."
    return 400, message


def add_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def validation_exception_handler(
        _request: Request, exc: RequestValidationError | ValidationError
    ) -> JSONResponse:
        """Each error points at the failed input, e.g. `#/body/title` or `#/query/limit`."""
        errors = [{"detail": e["msg"], "pointer": "#/" + "/".join(map(str, e["loc"]))} for e in exc.errors()]
        return problem(422, errors[0]["detail"] if errors else "Invalid input", errors)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        response = problem(exc.status_code, str(exc.detail))
        response.headers.update(exc.headers or {})
        return response

    @app.exception_handler(OpenPechaError)
    async def openpecha_exception_handler(_request: Request, exc: OpenPechaError) -> JSONResponse:
        return problem(exc.status_code, exc.message)

    @app.exception_handler(Neo4jClientError)
    async def neo4j_client_error_handler(_request: Request, exc: Neo4jClientError) -> JSONResponse:
        code = getattr(exc, "code", "unknown")
        status_code, message = _map_neo4j_client_error(code, str(exc))
        logger.warning("Neo4j client error (%s): %s", code, exc)
        return problem(status_code, message)

    @app.exception_handler(OpenSearchException)
    async def search_exception_handler(_request: Request, _exc: OpenSearchException) -> JSONResponse:
        return problem(503, "Search is temporarily unavailable")

    @app.exception_handler(Exception)
    async def general_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.error("Exception occurred: %s - %s", type(exc).__name__, str(exc), exc_info=exc)
        return problem(500, str(exc))
