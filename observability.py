"""
OpenTelemetry instrumentation beyond FastAPI's built-in telemetry.

FastAPI traces requests and, when OTEL_ENABLED=true, exports traces, metrics, and logs
to Grafana Cloud (or any OTLP-compatible backend) using the standard OTEL_* variables:
    OTEL_SERVICE_NAME           - logical service name shown in Grafana
    OTEL_RESOURCE_ATTRIBUTES    - e.g. deployment.environment=prod
    OTEL_EXPORTER_OTLP_ENDPOINT - Grafana Cloud OTLP endpoint, e.g.
                                  https://otlp-gateway-prod-us-east-0.grafana.net/otlp
    OTEL_EXPORTER_OTLP_HEADERS  - auth header, e.g.
                                  Authorization=Basic <base64(instanceId:token)>

This module adds trace IDs to Python log records. `Database` traces each Neo4j transaction.
"""

import logging

from opentelemetry.instrumentation.logging import LoggingInstrumentor

from config import settings

logger = logging.getLogger(__name__)


def setup_telemetry() -> None:
    if not settings.otel_enabled:
        logger.info("OpenTelemetry disabled (set OTEL_ENABLED=true to activate)")
        return

    LoggingInstrumentor().instrument(set_logging_format=True)
    logger.info("OpenTelemetry initialized")
