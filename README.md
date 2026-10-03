# RAGProof

RAGProof is a vendor-neutral differential debugger and evidence-lineage system
for production retrieval-augmented generation (RAG) applications.

It is designed to consume traces from OpenTelemetry-compatible applications,
Langfuse, or LangSmith and answer four operational questions. The local OTLP
path exists; the vendor import adapters have not been live-validated:

1. Which response claims are unsupported, contradicted, or stale?
2. Which pipeline component most likely caused the failure?
3. Which controlled change fixes the failure?
4. Which earlier answers are affected when knowledge or configuration changes?

## Current status

This is a **local MVP / work-in-progress snapshot**, not a production-ready
release. Start with these two detailed documents:

- [Complete project context and stage-by-stage handoff](docs/PROJECT_HANDOFF.md)
- [Remaining work, priorities, and acceptance criteria](docs/REMAINING_WORK.md)

Publication-day checks (2026-10-03): 54 tests passed and 48 infrastructure tests
were skipped. Real-CUAD positive support preservation passed for 96 original
annotations; this is not a hallucination-accuracy benchmark. Recent hardening
needs full validation, and the registered `ragproof-admin` implementation is
currently missing. See the handoff for exact limitations.

Stage 0 defines the MVP contract. Stage 1 adds a deterministic RAG laboratory
over real CUAD data. Stage 2 instruments the complete pipeline with
OpenTelemetry and maps captured spans into the canonical RAGProof trace. Stage
3 persists those traces, content objects, embeddings, and evidence lineage in
PostgreSQL/pgvector, S3-compatible storage, and Redis. Stage 4 extracts claims,
aligns them to the exact captured context, persists claim-level verdicts, and
processes ingestion events through a Redis consumer. Stage 5 ranks root-cause
hypotheses from recorded pipeline evidence and preserves them as suspected
until replay or human review confirms them. Stage 6 performs a clean baseline
rerun, changes exactly one diagnosed variable, compares both runs, and records
whether the hypothesis is confirmed, rejected, or needs review.
Stage 7 follows direct document-to-answer lineage to measure change blast radius
and promotes only confirmed real-data replays into immutable regression cases.
Stage 8 provides a local incident console for filtering those diagnoses and
inspecting their verdicts, replay evidence, lineage, and regression coverage.
Stage 9 adds a process-grade CI quality gate that reruns a candidate against
confirmed real-CUAD regression cases, persists an auditable report, and fails
closed when reliability or coverage thresholds are not met.
Stage 10 adds a strict OTLP/HTTP receiver for external RAG applications. It
accepts protobuf or JSON exports, normalizes complete RAG span trees into the
canonical contract, and rejects incomplete lineage instead of guessing.

- [Product contract](docs/product-contract.md)
- [Canonical trace contract](docs/canonical-trace.md)
- [Failure taxonomy](docs/failure-taxonomy.md)
- [Privacy and security contract](docs/privacy-security.md)
- [Stage 2 OpenTelemetry SDK](docs/stage-2-opentelemetry.md)
- [Stage 3 storage and evidence lineage](docs/stage-3-storage-lineage.md)
- [Stage 4 claim-evidence verification](docs/stage-4-claim-verification.md)
- [Stage 5 root-cause diagnosis](docs/stage-5-root-cause-diagnosis.md)
- [Stage 6 differential replay](docs/stage-6-differential-replay.md)
- [Stage 7 change impact and regressions](docs/stage-7-change-impact-regressions.md)
- [Stage 8 incident console](docs/stage-8-incident-console.md)
- [Stage 9 CI quality gate](docs/stage-9-ci-quality-gate.md)
- [Stage 10 external OpenTelemetry integration](docs/stage-10-external-otel-integration.md)
- Machine-readable contracts in [`schemas/`](schemas/)
- Valid contract examples in [`examples/`](examples/)

## MVP boundary

The MVP will provide OpenTelemetry ingestion, one Langfuse adapter, one
LangSmith adapter, claim-to-evidence verification, deterministic root-cause
classification, two-version differential replay, document change-impact
analysis, an incident view, and a CI quality gate.

RAGProof will not initially replace Langfuse or LangSmith, host models, provide
a vector database, manage prompts, or claim to determine objective truth from
unverified source documents.

## Validate the contracts

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,api,otlp,storage]'
.venv/bin/python -m unittest discover -s tests -v
```

## Run the Stage 1 laboratory

```bash
PYTHONPATH=src python3 -m ragproof_lab --list
PYTHONPATH=src python3 -m ragproof_lab --scenario healthy
PYTHONPATH=src python3 -m ragproof_lab --scenario generation_failure
```

The optional API can be run after installing the `api` extra:

```bash
python3 -m pip install -e '.[api]'
uvicorn ragproof_lab.api:app --reload
```

Capture a real CUAD run as a canonical metadata-only trace:

```bash
.venv/bin/ragproof-capture --scenario healthy
```

Content capture must be selected explicitly:

```bash
.venv/bin/ragproof-capture --scenario healthy --capture-mode full
```

Run the Stage 3 data plane locally:

```bash
docker compose -f infrastructure/docker-compose.lab.yml up -d
RAGPROOF_RUN_STORAGE_INTEGRATION=1 .venv/bin/python -m unittest \
  tests.test_storage_integration -v
.venv/bin/uvicorn ragproof_store.api:create_app --factory --port 8080
```

The Stage 8 incident console is then available at
`http://127.0.0.1:8080/dashboard`.

Process ingested traces with the Stage 4 verification worker:

```bash
.venv/bin/ragproof-verify-worker --once
.venv/bin/ragproof-diagnose-worker --once
.venv/bin/ragproof-replay-worker --once
.venv/bin/ragproof-regression-worker --once
```

The storage API exposes `POST /v1/verifications/{response_id}` for an explicit
evaluation and `GET /v1/verifications/{response_id}` for the persisted result.
Root-cause analysis is available through `POST /v1/diagnoses/{response_id}` and
`GET /v1/diagnoses/{response_id}`.
Differential replay is available through
`POST /v1/replays/diagnoses/{diagnosis_id}` and
`GET /v1/replays/{replay_id}`.
Document change impact is available through
`POST /v1/impact/documents/{document_id}/analyze` and
`GET /v1/impact/reports/{impact_id}`. Confirmed replays become regression cases
through `POST /v1/regressions/replays/{replay_id}` and can be read at
`GET /v1/regressions/{case_id}`.
The incident console reads `GET /v1/incidents` and
`GET /v1/incidents/{diagnosis_id}`.

Run the Stage 9 gate after at least one confirmed replay has been promoted to a
regression case:

```bash
.venv/bin/ragproof-gate \
  --minimum-cases 1 \
  --minimum-pass-rate 1.0
```

The command exits `0` for `PASS` and `1` for `FAIL`. The API equivalents are
`POST /v1/quality-gates` and `GET /v1/quality-gates/{gate_run_id}`.

Send a real CUAD application run through the same OTLP/HTTP path an external
service uses:

```bash
.venv/bin/python scripts/send_real_cuad_otlp.py \
  --endpoint http://127.0.0.1:8080/v1/otlp/traces
```

The receiver endpoint is `POST /v1/otlp/traces`. It supports
`application/x-protobuf`, `application/json`, and optional gzip encoding.

The laboratory deliberately uses a deterministic local generator and hashed
embeddings in tests over a pinned subset of the real, expert-annotated CUAD v1
contract dataset. This prevents external model changes, API keys, latency, and
cost from corrupting the human ground truth. Production model adapters will
implement the same interfaces later. See [third-party notices](THIRD_PARTY_NOTICES.md).

## Repository policy

The current source snapshot is published to
[`yatharth7115/ragproof`](https://github.com/yatharth7115/ragproof).
Secrets, private captured model content, backups, and generated runtime data
must never be committed. The public CUAD subset is tracked with attribution.
The project source-code license has not yet been selected.
