# Stage 9: CI quality gate

Stage 9 converts the confirmed regression evidence from Stage 7 into an
enforceable release decision. It runs a declared candidate pipeline against
frozen, real-data expectations and produces both a versioned JSON report and a
process exit code.

## Decision model

The gate selects the newest confirmed case for each `(query_hash, category)`
pair. This prevents repeated lab runs from overweighting one failure while
retaining category coverage. Every selected case must identify CUAD v1 under
CC BY 4.0 and contain a confirmed replay identifier.

For each case, the gate checks:

- the candidate used the same real query hash;
- every candidate claim verdict is allowed by the frozen expectation; and
- the recorded citation supports the claim when required.

The release policy is explicit:

- `minimum_cases` defaults to `1`;
- `minimum_pass_rate` defaults to `1.0`; and
- `required_categories` can require named failure classes to be represented.

An empty regression set fails closed. A lower pass-rate threshold can be used
deliberately, but the default requires every selected case to pass.

## CLI

With PostgreSQL, Redis, and MinIO running:

```bash
.venv/bin/ragproof-gate \
  --candidate-name main \
  --minimum-cases 1 \
  --minimum-pass-rate 1.0 \
  --required-category GENERATION_FAILURE
```

Exit status `0` means `PASS`; exit status `1` means `FAIL`. `--full-report`
prints the entire versioned result. Candidate component-mode flags exist for
the controlled local laboratory and make the negative path reproducible.

## API

Run a gate:

```http
POST /v1/quality-gates
Content-Type: application/json

{
  "candidate": {"name": "main"},
  "policy": {
    "minimum_cases": 1,
    "minimum_pass_rate": 1.0,
    "required_categories": ["GENERATION_FAILURE"]
  }
}
```

Read the immutable report:

```text
GET /v1/quality-gates/{gate_run_id}
```

Reports are stored in MinIO by content hash and indexed in PostgreSQL together
with case-level outcomes. Redis publishes `quality_gate.completed` for later
notification integrations.

## GitHub Actions

`.github/workflows/ragproof-quality-gate.yml` starts the local data plane,
runs the real-data integration suite to establish confirmed regression cases,
backfills regression cases from only the persisted `CONFIRMED` replays, then
executes the gate. The workflow requires all seven controlled failure
categories and a 100% pass rate.

The backfill is intentionally database-driven rather than timing-dependent on
Redis delivery:

```bash
.venv/bin/ragproof-regression-worker --backfill-confirmed
```

In production, point the same CLI at a durable regression-case environment so
that CI tests cases accumulated from real incidents instead of rebuilding the
database on every job.

## Real-data and privacy guarantees

No question, answer, passage, or expected verdict is generated for Stage 9.
The candidate runs against the pinned expert-annotated CUAD fixture, and all
expectations come from Stage 6 replays that Stage 7 accepted only after a
`CONFIRMED` outcome. The controlled component modes alter pipeline behaviour;
they do not invent test content or labels.

Quality-gate spans contain only run identifiers, candidate name, counts, and
outcome. CUAD query and answer text remains in content-addressed storage.

## Current boundary

The Stage 9 candidate adapter presently targets the deterministic local CUAD
laboratory. The gate contract, policy, persistence, HTTP surface, and exit-code
semantics are production-shaped; accepting an arbitrary external pipeline
endpoint is the next adapter task and is not falsely claimed here.

## Validate

```bash
RAGPROOF_RUN_STORAGE_INTEGRATION=1 .venv/bin/python -m unittest \
  tests.test_quality_gate_integration -v
```

The suite proves a healthy candidate passes, a controlled broken generator
fails against the same real case, empty coverage fails closed, reports round
trip through the API and storage, CLI exit codes are stable, and telemetry does
not expose CUAD content.
