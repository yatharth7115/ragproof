"""OpenTelemetry instrumentation and canonicalization for RAGProof."""

from .capture import CapturedRun, capture_configuration, capture_scenario
from .tracer import RagProofTracer, configure_otlp_provider


def __getattr__(name):
    # Protobuf is an optional transport dependency, not a dependency of capture.
    if name in {"OtlpCanonicalAdapter", "decode_export_request", "encode_export_json"}:
        from . import otlp_adapter

        return getattr(otlp_adapter, name)
    raise AttributeError(name)

__all__ = [
    "CapturedRun",
    "RagProofTracer",
    "capture_scenario",
    "capture_configuration",
    "configure_otlp_provider",
    "OtlpCanonicalAdapter",
    "decode_export_request",
    "encode_export_json",
]
