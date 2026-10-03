# Stage 1: Controlled RAG Failure Laboratory

## Purpose

The laboratory is the reference system used to develop and measure RAGProof.
It is deliberately small, deterministic, and instrumentable. Every failure is
introduced at exactly one pipeline boundary while other components remain
fixed.

This is a test instrument, not the production RAGProof service.

## Pipeline

```text
real CUAD contract fixtures
  → contract-text parser
  → block chunker
  → deterministic hashed embeddings
  → hybrid lexical/vector retrieval
  → reranker
  → context selection
  → deterministic policy-answer model
  → answer and citation
```

The documents, questions, answers, and evidence spans come from CUAD v1, a
CC BY 4.0 corpus of real commercial contracts with expert-supervised labels.
The local model implements the future provider interface but uses the human
annotation as its extractive reference. A live LLM is unsuitable as the source
of truth for regression tests because its behavior can change independently of
RAGProof.

## Scenarios

| Scenario | Injected boundary | Observable signature |
|---|---|---|
| `healthy` | None | The annotated 15-day notice clause reaches the answer and citation |
| `parsing_failure` | Parser | Source contains the policy row; parsed blocks do not |
| `chunking_failure` | Chunker | Parsed block contains the evidence; no individual chunk does |
| `retrieval_failure` | Retriever | Indexed evidence exists; retrieved candidates omit it |
| `reranking_failure` | Reranker | Pre-rerank candidates contain evidence; final context omits it |
| `generation_failure` | Generator | Final context contains evidence; answer contradicts it |
| `citation_failure` | Citation selection | Answer is correct; cited chunk does not support it |
| `stale_knowledge` | Corpus selection | Evidence is supported but carries a superseded index-snapshot version |

Each run returns the complete artifacts necessary for later diagnosis:
source versions, parsed blocks, chunks, pre- and post-rerank candidates, final
context, answer, citation, component versions, and injected ground-truth label.

## Ground-truth rule

Scenario labels describe controlled transformations applied to real source
material. Transformed artifacts are never represented as original CUAD data.
The future diagnosis engine must infer the fault from artifacts and may not read
the injected label. Tests will prevent the diagnosis package from importing
scenario internals.

## Dataset provenance

The tracked fixture is produced by `scripts/build_cuad_fixture.py` from the
official CUAD archive. The archive SHA-256 is pinned, its CC BY 4.0 attribution
is preserved in `THIRD_PARTY_NOTICES.md`, and the derived fixture records its
source URL and transformation. Tests run against the tracked subset without
network access. Rebuilding it downloads the archive directly and requires no
API key.

## API

When the optional FastAPI dependency is installed:

- `GET /health`
- `GET /v1/scenarios`
- `POST /v1/runs/{scenario}`

## PostgreSQL fixture

`infrastructure/docker-compose.lab.yml` supplies PostgreSQL with pgvector for
integration work. The deterministic unit laboratory remains in-memory so it can
run in CI without containers. Stage 3 will make durable trace and lineage
storage part of the product runtime.

## Acceptance criteria

- All eight scenarios run without network access or secrets.
- The healthy scenario returns the human-annotated 15-day clause and correct citation.
- Each failure exposes the boundary-specific observable signature in the table.
- Repeating a scenario produces identical semantic artifacts.
- CLI output is valid JSON.
- The optional API delegates to the same pipeline used by tests and CLI.
