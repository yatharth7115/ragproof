from __future__ import annotations

from dataclasses import dataclass

from opentelemetry.context import Context, attach, detach
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ragproof_lab.models import RunArtifacts
from ragproof_lab.scenarios import Scenario

from .canonical import build_canonical_trace
from .tracer import RagProofTracer


@dataclass(frozen=True)
class CapturedRun:
    artifacts: RunArtifacts
    spans: tuple[ReadableSpan, ...]
    canonical_trace: dict


def capture_scenario(
    scenario: str,
    capture_mode: str = "metadata_only",
) -> CapturedRun:
    """Run a real-data lab scenario and capture its complete OTel trace."""

    # Local import avoids a package cycle during module initialization.
    from ragproof_lab.pipeline import run_scenario

    return _capture(lambda telemetry: run_scenario(
        scenario, telemetry=telemetry, capture_mode=capture_mode
    ), capture_mode)


def capture_configuration(
    configuration: Scenario, capture_mode: str = "metadata_only"
) -> CapturedRun:
    """Capture a declared pipeline configuration over the real CUAD fixture."""

    from ragproof_lab.pipeline import run_configuration

    return _capture(lambda telemetry: run_configuration(
        configuration, telemetry=telemetry, capture_mode=capture_mode
    ), capture_mode)


def _capture(run_pipeline, capture_mode: str) -> CapturedRun:

    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "ragproof-cuad-lab"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("ragproof", "0.1.0")
    telemetry = RagProofTracer(tracer=tracer, capture_mode=capture_mode)

    context_token = attach(Context())
    try:
        artifacts = run_pipeline(telemetry)
    finally:
        detach(context_token)
    spans = tuple(exporter.get_finished_spans())
    canonical = build_canonical_trace(artifacts, spans, capture_mode)
    provider.shutdown()
    return CapturedRun(artifacts=artifacts, spans=spans, canonical_trace=canonical)
