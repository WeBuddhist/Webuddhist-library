from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load once for both typed application settings and AWS/OpenTelemetry SDKs.
load_dotenv()


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(extra="ignore")

    neo4j_uri: str = ""
    neo4j_username: str = ""
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"

    aws_s3_bucket: str = ""
    aws_region: str = ""

    opensearch_endpoint: str = ""
    opensearch_index: str = "content-search"
    opensearch_catalog_index: str = "catalog-search"
    opensearch_auth_mode: str = "none"
    opensearch_username: str = ""
    opensearch_password: str = ""

    environment: str = ""
    otel_enabled: bool = False

    # Release shown in Swagger and /__/health; the deploy writes the tag (prod) or branch-sha (dev, testing).
    app_version: str = ""
    # Public path prefix in front of the API, for example "/api" behind library.webuddhist.com/api.
    root_path: str = ""


TESTING_ENVIRONMENT = "testing"
TESTING_INDEX_PREFIX = "testing-"


def check_environment(config: Settings) -> None:
    """Refuse settings that would let one environment write into another environment's data.

    Testing shares the prod OpenSearch domain, so its indexes must carry their own prefix.
    """
    if config.environment != TESTING_ENVIRONMENT:
        return
    wrong = [
        name
        for name in (config.opensearch_index, config.opensearch_catalog_index)
        if not name.startswith(TESTING_INDEX_PREFIX)
    ]
    if wrong:
        raise RuntimeError(
            f"ENVIRONMENT={TESTING_ENVIRONMENT} needs search index names that start with "
            f"{TESTING_INDEX_PREFIX!r}; got {', '.join(wrong)}"
        )


settings = Settings()
