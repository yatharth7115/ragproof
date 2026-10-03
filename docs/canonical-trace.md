# Canonical RAG Trace Contract

The canonical trace is RAGProof's vendor-neutral interchange format. Adapters
from Langfuse and LangSmith map their native structures to this contract, while
direct integrations emit the same information through OpenTelemetry.

The normative machine-readable definition is
[`schemas/canonical-trace.schema.json`](../schemas/canonical-trace.schema.json).

## Required identity and versioning

Every trace contains:

- `schema_version`: version of this contract.
- `trace_id`: stable identifier for one end-to-end request.
- `tenant_id` and `project_id`: isolation boundary.
- `started_at` and `ended_at`: UTC timestamps.
- `source`: direct OpenTelemetry, Langfuse, or LangSmith.
- `pipeline`: immutable component versions.
- `knowledge_base`: corpus and index versions.
- `query`, `retrieval`, and `generation`: normalized RAG activity.
- `privacy`: capture mode and redaction state.

## Pipeline identity

The `pipeline.fingerprint` is a deterministic hash of all behavior-affecting
configuration, including parser, chunker, embedder, retriever, reranker, prompt,
generator, and corpus versions. Secrets and timestamps must not affect it.

Changing any behavior-affecting value creates a new fingerprint.

## Content fields

Content-bearing objects use three complementary fields:

- `content`: captured text when policy permits.
- `content_hash`: SHA-256 or an equivalently documented stable hash.
- `content_ref`: reference to encrypted object storage or the customer's store.

`full` mode requires content or a content reference. `redacted` mode stores
sanitized content. `metadata_only` mode permits only hashes and identifiers.
The first schema version validates structure; ingestion policy enforces these
cross-field privacy rules.

## Retrieval candidates

Retrieval records preserve both pre-rerank and final ranks when available. A
candidate also records whether it was included in the model context. This makes
it possible to distinguish retrieval, reranking, and generation failures.

## OpenTelemetry mapping

RAGProof uses standard `gen_ai.*` attributes where their documented semantics
fit. RAG-specific fields without stable standard equivalents use the versioned
`ragproof.*` namespace.

Suggested spans:

```text
rag.request
├── document.parse
├── document.chunk
├── embedding.create
├── retrieval.search
├── retrieval.rerank
├── prompt.construct
├── llm.generate
└── ragproof.evaluate
```

Raw vendor identifiers and unmapped properties belong in `extensions`, namespaced
by provider. They must never silently override canonical fields.

## Compatibility rules

- Additive optional fields are permitted within a minor contract version.
- Removing, renaming, or changing the meaning of a field requires a major
  version.
- Adapters must record their own name and version.
- Unknown extension fields must be preserved during read/write cycles.
- Invalid or incomplete source traces are quarantined with validation errors;
  they are not silently repaired.
