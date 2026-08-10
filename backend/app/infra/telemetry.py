"""OpenTelemetry bootstrap.

M-01 only establishes the base capability: API requests and the smoke
Workflow/Activity land in the same trace, exported to the local OTLP collector.
Dashboards/alerting are M-17.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config import Settings

if TYPE_CHECKING:
    from fastapi import FastAPI


def setup_otel(settings: Settings) -> None:
    """Configure the global tracer provider. Idempotent-safe for M-01 use."""
    if not settings.otel_enabled:
        return

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": settings.service_name}))
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint, insecure=True)
        )
    )
    trace.set_tracer_provider(provider)


def init_fastapi_telemetry(app: FastAPI, settings: Settings) -> None:
    if not settings.otel_enabled:
        return

    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)
