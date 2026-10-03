"""Bounded complete-trace export for RAGProof's strict OTLP ingestion profile."""

from __future__ import annotations

from collections import OrderedDict
from threading import Lock

from opentelemetry.sdk.trace import SpanProcessor
from opentelemetry.sdk.trace.export import SpanExportResult


class CompleteTraceSpanProcessor(SpanProcessor):
    """Export children with their completed rag.request root, in one request.

    Export is synchronous at root completion. Applications needing asynchronous
    delivery should call their tracing code off the request event loop. Generic
    BatchSpanProcessor may split traces across batches, which this receiver
    deliberately does not reconstruct. The buffers have explicit bounds.
    """

    def __init__(self, exporter, max_pending_traces=128, max_spans_per_trace=4096):
        self.exporter = exporter
        self.max_pending_traces = max_pending_traces
        self.max_spans_per_trace = max_spans_per_trace
        self._pending = OrderedDict()
        self._lock = Lock()
        self._successful = True
        self._closed = False

    def on_start(self, span, parent_context=None):
        return None

    def on_end(self, span):
        context = span.get_span_context()
        with self._lock:
            if self._closed:
                return
            if context.trace_id not in self._pending:
                if len(self._pending) >= self.max_pending_traces:
                    self._pending.popitem(last=False)
                    self._successful = False
                self._pending[context.trace_id] = []
            batch = self._pending[context.trace_id]
            if len(batch) >= self.max_spans_per_trace:
                self._successful = False
                # Keep the bounded prefix; it is never reported as successful.
                return
            batch.append(span)
            if span.name != "rag.request":
                return
            del self._pending[context.trace_id]
        try:
            successful = self.exporter.export(batch) is SpanExportResult.SUCCESS
        except Exception:
            successful = False
        with self._lock:
            self._successful = self._successful and successful

    def force_flush(self, timeout_millis=30000):
        with self._lock:
            return self._successful and not self._pending

    def shutdown(self):
        with self._lock:
            self._closed = True
            self._pending.clear()
        self.exporter.shutdown()
