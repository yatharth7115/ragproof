# Stage 10: external OpenTelemetry integration

Stage 10 moves RAGProof beyond its in-process laboratory. A separately
configured application can export an OTLP trace tree over HTTP, and RAGProof
will validate, normalize, persist, verify, diagnose, and display that trace
through the existing pipeline.

## Receiver

```text
POST /v1/otlp/traces
```

The receiver implements the OTLP `ExportTraceServiceRequest` envelope and
accepts both official HTTP encodings:

- `Content-Type: application/x-protobuf`
- `Content-Type: application/json`

`Content-Encoding: gzip` is supported. Compressed and expanded request bodies
are capped at 16 MiB. A successful response is an empty
`ExportTraceServiceResponse` in the same media type as the request.

RAGProof uses `/v1/otlp/traces` rather than `/v1/traces` because the latter is
already the canonical JSON ingestion endpoint.

## Strict normalization

The adapter requires one `rag.request` root and the following child spans:

```text
retrieval.search
retrieval.rerank
prompt.construct
llm.generate
```

The trace must declare:

- tenant and project identifiers;
- privacy capture mode and retention;
- all eight pipeline component versions;
- data source, corpus, and index versions;
- query and response SHA-256 hashes;
- aligned chunk, document, version, hash, score, and rank arrays;
- the chunks included in model context; and
- model provider, model name, and citation lineage when a citation exists.

Missing or misaligned lineage produces HTTP 400. The adapter does not invent
component versions, chunk relationships, or content.

Standard `gen_ai.*` attributes are used where OpenTelemetry defines matching
semantics. RAG-only fields use the versioned `ragproof.*` namespace.

## Application setup

Install the OTLP extra and point the exporter at RAGProof:

```python
from ragproof_otel import RagProofTracer, configure_otlp_provider

provider = configure_otlp_provider(
    endpoint="http://ragproof:8080/v1/otlp/traces",
    service_name="customer-support-rag",
)
telemetry = RagProofTracer(
    tracer=provider.get_tracer("customer-support-rag"),
    capture_mode="metadata_only",
)
```

The application owns its OpenTelemetry provider and can add its own sampling,
resource attributes, processors, and authentication headers. RAGProof does not
replace the process-global provider.

For the complete attribute mapping, use the Stage 10 instrumented CUAD pipeline
in `src/ragproof_lab/pipeline.py` as the executable reference.

## Real-data smoke run

Start RAGProof and send an independently exported CUAD trace:

```bash
docker compose -f infrastructure/docker-compose.lab.yml up -d --wait
.venv/bin/uvicorn ragproof_store.api:create_app --factory --port 8080
.venv/bin/python scripts/send_real_cuad_otlp.py
```

The script runs the pinned expert-annotated CUAD case and sends it through the
actual OTLP protobuf exporter. It does not construct a fake OTLP payload.

## Privacy

In `full` mode, content arrives in OTLP attributes, is immediately moved into
content-addressed object storage, and is removed from the PostgreSQL span and
canonical-trace records. `metadata_only` remains the default and transmits no
query, response, or document text.

The integration tests assert that the stored trace contains none of the real
CUAD query or answer while the verifier can resolve the private object
references and produce a grounded verdict.

## Validate

```bash
RAGPROOF_RUN_STORAGE_INTEGRATION=1 .venv/bin/python -m unittest \
  tests.test_external_otlp_integration -v
```

The suite uses an actual OpenTelemetry OTLP exporter and the real CUAD fixture.
It covers protobuf, JSON, canonical fingerprint preservation, durable content
lineage, claim verification, idempotency, and strict rejection.

## Vendor adapters still requiring credentials

Direct OTLP does not require an API key. Live Langfuse and LangSmith imports
cannot be honestly validated without access to real traces. Those adapters are
the next Stage 10 increment and require one of:

- Langfuse: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and
  `LANGFUSE_BASE_URL`; or
- LangSmith: `LANGSMITH_API_KEY` and the target project name.

No credentials should be committed to this repository.
