from __future__ import annotations

from jsonschema import Draft202012Validator, FormatChecker
from ragproof_resources import load_schema

from ragproof_otel import OtlpCanonicalAdapter, decode_export_request

from .repository import TraceRepository


class OtlpTraceIngestor:
    """Receive OTLP/HTTP export batches and persist complete RAG traces."""

    def __init__(
        self,
        repository: TraceRepository,
        adapter: OtlpCanonicalAdapter | None = None,
    ) -> None:
        self.repository = repository
        self.adapter = adapter or OtlpCanonicalAdapter()
        self.validator = Draft202012Validator(
            load_schema("canonical-trace.schema.json"),
            format_checker=FormatChecker(),
        )

    def ingest(self, payload: bytes, content_type: str, authorize=None) -> list[dict]:
        export_request = decode_export_request(payload, content_type)
        converted = self.adapter.convert(export_request)
        for trace, _ in converted:
            errors = sorted(
                self.validator.iter_errors(trace), key=lambda error: list(error.path)
            )
            if errors:
                paths = ["/".join(str(part) for part in error.absolute_path) for error in errors]
                raise ValueError("Canonical trace contract violation at: " + ", ".join(paths))
            if authorize is not None:
                authorize(trace)
        # Validate and authorize the entire request before the first write.
        return [self.repository.ingest(trace) for trace, _ in converted]
