# Stage 3: durable storage and evidence lineage

Stage 3 turns the canonical trace into a queryable system of record. Its proof
fixture is a full OpenTelemetry capture of the real, expert-annotated CUAD v1
contract subset. No generated dataset, hosted model, paid API, or API key is
used.

## Data plane

- PostgreSQL stores tenants, projects, canonical traces, spans, queries,
  responses, document versions, chunks, retrieval ranks, and context selection.
- pgvector stores the 64-dimensional laboratory embedding of each real CUAD
  chunk. The deterministic embedding isolates storage behavior from external
  model drift; it is not presented as a production semantic model.
- MinIO provides the local S3-compatible content plane. Full-capture text is
  content-addressed by SHA-256 and the database retains only its object
  reference. Metadata-only and redacted traces never create content objects.
- Redis Streams receives a `trace.ingested` event after the database transaction
  commits, providing the Stage 4 worker boundary.

The local ports are deliberately non-default to avoid collisions: PostgreSQL
`55432`, Redis `56379`, MinIO API `59000`, and MinIO console `59001`.

## Lineage guarantees

Every ingested answer can be followed through:

```text
response -> trace -> pipeline fingerprint -> retrieval candidate
         -> exact chunk hash -> document version -> data source
```

`GET /v1/lineage/answers/{response_id}` performs the forward lookup. It returns
the answer and query hashes/references, pipeline and knowledge-base versions,
and every retrieved chunk with retrieval rank, rerank rank, score, and
`included_in_context` status.

`GET /v1/impact/documents/{document_id}` performs the reverse lookup when given
`tenant_id`, `project_id`, and `document_version`. It returns each affected
answer once, plus the exact chunk occurrences that connect the answer to that
document version. This is the first usable blast-radius primitive.

Chunk identity includes tenant, project, chunk ID, and content hash. This avoids
silently merging two chunks that reuse an ID after their content changes. A
later full trace may fill content and embedding fields first observed by a
metadata-only trace, but it cannot overwrite a different hash.

## Privacy boundary

The canonical JSON saved in PostgreSQL never contains raw query, answer,
retrieved chunk, prompt-context, or span content. Full content is moved to the
object store only after its declared SHA-256 is verified. Object keys use a
hashed tenant/project namespace so user-controlled identifiers do not become
paths. The object store adapter rejects references outside its configured
bucket.

The Compose credentials are local development defaults only. Production must
inject secrets, use TLS, apply tenant-scoped authorization, configure retention,
and replace the single-node services with managed or highly available systems.

## Run the real integration proof

```bash
.venv/bin/python -m pip install -e '.[dev,api,otlp,storage]'
docker compose -f infrastructure/docker-compose.lab.yml up -d
RAGPROOF_RUN_STORAGE_INTEGRATION=1 \
  .venv/bin/python -m unittest tests.test_storage_integration -v
```

The proof checks real text round-trips through MinIO, database JSON contains no
raw CUAD text, every captured candidate has a 64-dimensional pgvector value,
duplicate trace ingestion is idempotent, Redis receives the event, answer
lineage resolves to the chosen evidence, and the source document resolves back
to the affected answer.
