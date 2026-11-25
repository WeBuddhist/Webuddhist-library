import logging
import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from http import HTTPStatus

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from neo4j.exceptions import ClientError as Neo4jClientError
from opensearchpy.exceptions import OpenSearchException
from pydantic import BaseModel, ValidationError
from starlette.exceptions import HTTPException

from catalog_search import CatalogSearchService
from config import check_environment, settings
from content_search import ContentSearchService
from database import Database
from dependencies import get_api_key
from exceptions import InvalidRequestError, OpenPechaError
from observability import setup_telemetry
from routers.alignments import router as alignments_router
from routers.annotations import router as annotations_router
from routers.applications import router as applications_router
from routers.audios import router as audios_router
from routers.categories import router as categories_router
from routers.content_search import router as content_search_router
from routers.editions import router as editions_router
from routers.languages import router as languages_router
from routers.persons import router as persons_router
from routers.recordings import router as recordings_router
from routers.segments import router as segments_router
from routers.tags import router as tags_router
from routers.texts import router as texts_router
from search_client import create_search_client
from storage import Storage

logger = logging.getLogger(__name__)

_UNTRACED_PATHS = frozenset({"/__/health", "/docs", "/redoc", "/openapi.json"})

# Version of the API contract. A release tag (APP_VERSION) overrides it in Swagger and /__/health.
API_VERSION = "2.15.0"

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


def _map_neo4j_client_error(exc: Neo4jClientError) -> tuple[int, str]:
    """Map Neo4j client errors to concise API responses."""
    code = getattr(exc, "code", "")
    message = str(exc)

    if code == "Neo.ClientError.Transaction.TransactionHookFailed" or "Error executing triggers" in message:
        detail = message
        marker = "java.lang.RuntimeException:"
        if marker in message:
            detail = message.split(marker, 1)[1].strip().split(", enforce_", 1)[0].strip(" }")
        else:
            fallback = re.search(r"Error executing triggers \{([^=]+)=([^}]+?)\s*(?:,\s*[A-Za-z0-9_]+=|\})", message)
            if fallback:
                detail = f"{fallback.group(1).strip()}: {fallback.group(2).strip()}"
            else:
                detail = "Database validation failed due to trigger constraints."
        return 422, detail

    if "Constraint" in code:
        return 409, "Database constraint violation."

    return 400, message


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Lifespan context manager for startup/shutdown events."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(name)s - %(message)s")
    logger.info("Starting OpenPecha API")

    async with AsyncExitStack() as stack:
        setup_telemetry()

        if not app.state.testing and settings.neo4j_uri:
            check_environment(settings)
            app.state.db = await stack.enter_async_context(Database.from_settings(settings))
            app.state.storage = await stack.enter_async_context(Storage.open(settings))

            if settings.opensearch_endpoint:
                try:
                    search_client = create_search_client(settings, request_timeout=10, max_retries=0)
                    stack.push_async_callback(search_client.close)
                    content_search = ContentSearchService(client=search_client, index_name=settings.opensearch_index)
                    catalog_search = CatalogSearchService(
                        client=search_client, index_name=settings.opensearch_catalog_index
                    )
                    await content_search.setup_index()
                    await catalog_search.setup_index()
                except InvalidRequestError, BotoCoreError, ClientError, OpenSearchException, ValueError:
                    logger.exception("Search disabled until restart; canonical CRUD remains available")
                else:
                    app.state.content_search = content_search
                    app.state.catalog_search = catalog_search
            logger.info("Canonical stores initialized")
        else:
            logger.info("Skipping database initialization (testing mode or no NEO4J_URI)")
        yield

    logger.info("Shutting down OpenPecha API")


def create_app(*, testing: bool = False) -> FastAPI:
    app = FastAPI(
        title="OpenPecha API v2",
        version=settings.app_version or API_VERSION,
        root_path=settings.root_path.rstrip("/"),
        lifespan=lifespan,
        telemetry={
            "auto_configure": settings.otel_enabled,
            "exclude": lambda scope: scope["path"].removeprefix(scope.get("root_path", "")) in _UNTRACED_PATHS,
        },
    )

    app.state.testing = testing

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

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
        status_code, message = _map_neo4j_client_error(exc)
        logger.warning("Neo4j client error (%s): %s", getattr(exc, "code", "unknown"), message)
        return problem(status_code, message)

    @app.exception_handler(OpenSearchException)
    async def search_exception_handler(_request: Request, _exc: OpenSearchException) -> JSONResponse:
        return problem(503, "Search is temporarily unavailable")

    @app.exception_handler(Exception)
    async def general_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.error("Exception occurred: %s - %s", type(exc).__name__, str(exc), exc_info=exc)
        return problem(500, str(exc))

    @app.middleware("http")
    async def request_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        """Log all requests and add no-cache headers to all responses."""
        response = await call_next(request)
        logger.info(
            "Request: %s %s | Status: %d",
            request.method,
            request.url.path,
            response.status_code,
        )
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    @app.get("/__/health")
    async def health_check() -> dict:
        """Health check endpoint; also tells which release this environment runs."""
        return {"status": "healthy", "version": app.version}

    errors_doc = {"description": "Problem details", "content": {PROBLEM_JSON: {"schema": Problem.model_json_schema()}}}
    for router in (
        texts_router,
        editions_router,
        persons_router,
        categories_router,
        tags_router,
        languages_router,
        alignments_router,
        annotations_router,
        applications_router,
        content_search_router,
        segments_router,
        audios_router,
        recordings_router,
    ):
        app.include_router(router, dependencies=[Depends(get_api_key)], responses={"default": errors_doc})

    return app


app = create_app()
