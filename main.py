import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from opensearchpy.exceptions import OpenSearchException

from config import check_environment, settings
from database import Database
from dependencies import get_api_key
from errors import InvalidRequestError
from http_errors import PROBLEM_JSON, Problem, add_exception_handlers
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
from search.catalog import CatalogSearchService
from search.client import create_search_client
from search.content import ContentSearchService
from storage import Storage

logger = logging.getLogger(__name__)

_UNTRACED_PATHS = frozenset({"/__/health", "/docs", "/redoc", "/openapi.json"})

# Version of the API contract. A release tag (APP_VERSION) overrides it in Swagger and /__/health.
API_VERSION = "2.15.0"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Lifespan context manager for startup/shutdown events."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(name)s - %(message)s")
    logger.info("Starting OpenPecha API")

    async with AsyncExitStack() as stack:
        setup_telemetry()

        if settings.neo4j_uri:
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
            logger.info("Skipping database initialization (no NEO4J_URI)")
        yield

    logger.info("Shutting down OpenPecha API")


def create_app() -> FastAPI:
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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    add_exception_handlers(app)

    @app.middleware("http")
    async def no_cache_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
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
