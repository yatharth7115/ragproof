# Stage 2: OpenTelemetry SDK and Canonical Traces

## Outcome

Stage 2 instruments every boundary of the real CUAD laboratory and converts the
resulting OpenTelemetry spans into the versioned canonical RAGProof trace.

No model API or generated evaluation dataset is used. Trace tests execute the
human-annotated CUAD case pinned in Stage 1.

## Trace tree

```text
rag.request
├── document.parse
├── document.chunk
├── embedding.create
├── retrieval.search
├── retrieval.rerank
├── prompt.construct
└── llm.generate
```

All stage spans share one 128-bit trace ID and are direct children of the
request span. RAGProof uses standard `gen_ai.*` attributes where applicable and
versioned `ragproof.*` attributes for retrieval-specific lineage.

## SDK surface

`RagProofTracer` is a small provider-neutral facade around the OpenTelemetry
API. It accepts an injected OpenTelemetry tracer, so applications retain control
over sampling, processors, resources, and exporters.

```python
from ragproof_otel import RagProofTracer

telemetry = RagProofTracer(capture_mode="metadata_only")
with telemetry.span("rag.request", {"ragproof.project.id": "contracts"}):
    with telemetry.span("retrieval.search") as span:
        span.set_attribute("ragproof.retrieval.chunk_ids", ["chunk-1"])
```

`configure_otlp_provider` creates an isolated OTLP/HTTP provider for applications
that want RAGProof to configure export. It deliberately does not replace the
process-global provider.

## Capture modes

- `metadata_only` is the default. It records identifiers, hashes, ranks,
  versions, scores, timings, and token counts, but no query, response, evidence,
  or contract text.
- `redacted` records the literal marker `[REDACTED]` in content fields while
  retaining hashes for correlation.
- `full` records content and must be selected explicitly.

Tests search serialized metadata-only and redacted traces for the real CUAD
question, annotated answer, and evidence. Their presence fails the suite.

## Canonicalization

The canonicalizer produces:

- Trace and span identity with parent relationships.
- Immutable component hashes and one pipeline fingerprint.
- Corpus and index versions.
- Pre-rerank and post-rerank candidate information.
- Context inclusion decisions.
- Query, response, and chunk content hashes.
- Provider-neutral privacy metadata.
- CUAD provenance and license metadata.

The output validates against `schemas/canonical-trace.schema.json`.

## Commands

```bash
.venv/bin/ragproof-capture --scenario healthy
.venv/bin/ragproof-capture --scenario generation_failure --capture-mode full
.venv/bin/ragproof-capture --scenario healthy --output artifacts/trace.json
```

## OTLP export

Install the exporter extra and provide the destination explicitly:

```python
from ragproof_otel import configure_otlp_provider

provider = configure_otlp_provider(
    endpoint="http://localhost:4318/v1/traces",
    service_name="contract-assistant",
)
tracer = provider.get_tracer("contract-assistant")
```

Credentials, if required by a future Langfuse or LangSmith endpoint, must be
provided by the user through environment configuration and must never be stored
in source control.

## Acceptance criteria

- The real CUAD run emits all eight required spans.
- All spans share a trace and have the correct request parent.
- Canonical output validates against the contract schema.
- Pipeline fingerprints remain stable across independent requests.
- Metadata-only and redacted traces retain no real CUAD content.
- Full capture retains the real query, answer, and selected evidence.
- The CLI emits valid JSON and defaults to metadata-only capture.
