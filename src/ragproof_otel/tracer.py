from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Mapping, Sequence

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider


AttributeValue = str | bool | int | float | Sequence[str] | Sequence[bool] | Sequence[int] | Sequence[float]
ALLOWED_CAPTURE_MODES = {"full", "redacted", "metadata_only"}


class RagProofTracer:
    """Small vendor-neutral tracing facade used by RAG pipeline integrations."""

    def __init__(self, tracer=None, capture_mode: str = "metadata_only") -> None:
        if capture_mode not in ALLOWED_CAPTURE_MODES:
            raise ValueError(f"Unsupported capture mode: {capture_mode}")
        self.capture_mode = capture_mode
        self.tracer = tracer or trace.get_tracer("ragproof", "0.1.0")

    def span(
        self,
        name: str,
        attributes: Mapping[str, AttributeValue] | None = None,
    ) -> AbstractContextManager:
        return self.tracer.start_as_current_span(name, attributes=dict(attributes or {}))

    def content_attributes(self, prefix: str, content: str) -> dict[str, str]:
        if self.capture_mode == "metadata_only":
            return {}
        if self.capture_mode == "redacted":
            return {f"{prefix}.content": "[REDACTED]"}
        return {f"{prefix}.content": content}


def configure_otlp_provider(
    endpoint: str,
    service_name: str = "ragproof",
    headers: Mapping[str, str] | None = None,
) -> TracerProvider:
    """Create an OTLP/HTTP provider without changing the process-global provider."""

    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    except ImportError as error:
        raise RuntimeError("Install OTLP support with: pip install -e '.[otlp]'") from error

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    from .processor import CompleteTraceSpanProcessor

    exporter = OTLPSpanExporter(endpoint=endpoint, headers=dict(headers or {}))
    provider.add_span_processor(CompleteTraceSpanProcessor(exporter))
    return provider
