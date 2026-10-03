# Stage 7: change impact and regression cases

Stage 7 turns evidence lineage and confirmed differential replays into two
production-facing artifacts:

1. a document-version blast-radius report; and
2. an immutable regression case backed by a real observed failure and passing
   reference run.

## Direct-lineage impact

`ChangeImpactAnalyzer` accepts a tenant, project, document, old version, and
new version. It follows persisted relational links rather than running a new
similarity search:

```text
document version
  -> retrieved chunks
  -> context inclusion / citation
  -> response
  -> verified claims
  -> diagnoses and confirmed incidents
```

Each affected trace records one or more precise usage modes:

- `retrieved_only`: a chunk appeared in retrieval results but was not supplied
  to generation;
- `context`: a chunk was supplied to generation; and
- `citation`: the response cited a chunk from the version.

This distinction prevents a retrieved-but-unused document from being reported
as if it necessarily influenced the answer.

The report includes the content hashes observed for each version. Those hashes
are not a complete document manifest, so Stage 7 reports `content_change` as
`unknown`. A later ingestion adapter can assert `changed` or `unchanged` only
after it captures a complete version manifest or source document hash.

## Confirmed-replay regression cases

`RegressionCaseBuilder` accepts only a Stage 6 replay with outcome
`CONFIRMED`. A case stores content-addressed references and hashes for:

- the original real CUAD query;
- the reproduced failing baseline;
- the one-variable passing reference; and
- the expected claim and citation conditions.

`REJECTED` and `NEEDS_REVIEW` replays are deliberately refused. This keeps a
plausible diagnosis from silently becoming test ground truth.

The Redis regression worker consumes `replay.completed` events and promotes
only confirmed replays:

```bash
.venv/bin/ragproof-regression-worker --once
```

## API

Analyze a recorded document version transition:

```text
POST /v1/impact/documents/{document_id}/analyze
  ?tenant_id=...
  &project_id=...
  &from_version=...
  &to_version=...
```

Read and create artifacts:

```text
GET  /v1/impact/reports/{impact_id}
POST /v1/regressions/replays/{replay_id}
GET  /v1/regressions/{case_id}
```

The machine-readable contracts are
`schemas/change-impact.schema.json` and
`schemas/regression-case.schema.json`.

## Real-data guarantee and privacy

The Stage 7 integration suite uses the pinned, licensed CUAD v1 fixture and the
real stale-document scenario from Stages 1 and 6. It verifies that the stored
query reference resolves to the original CUAD question. No generated question,
answer, passage, or expected label is introduced.

OpenTelemetry spans contain identifiers, versions, categories, and counts, but
not CUAD query, evidence, or answer content. Full text remains content-addressed
in the configured object store.

## Validate

With the Stage 3 data plane running:

```bash
RAGPROOF_RUN_STORAGE_INTEGRATION=1 .venv/bin/python -m unittest \
  tests.test_impact_integration -v
```

The suite covers persisted impact reports, usage-mode classification, confirmed
incident linkage, regression promotion, rejection of non-confirmed replay,
worker consumption, API round trips, contract validation, and span privacy.
