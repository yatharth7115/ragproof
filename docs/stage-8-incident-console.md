# Stage 8: incident console

Stage 8 adds the first operator-facing RAGProof surface. It is a local incident
console backed directly by the PostgreSQL evidence plane built in Stages 3–7.
The dashboard contains no sample rows or generated incident content.

Open the console while the storage API is running:

```text
http://127.0.0.1:8080/dashboard
```

## Operator workflow

The first view shows:

- total recorded diagnoses;
- replay-confirmed incidents;
- incidents awaiting replay or review;
- rejected one-variable hypotheses; and
- the incident ledger with status and failure-category filters.

Selecting a row opens an evidence panel containing the diagnosed component,
claim verdicts, controlled replay outcome, regression-case coverage, lineage
counts and hashes, and the diagnosis lifecycle.

The view does not imply that every diagnosis is a production outage. A
`suspected` diagnosis remains a hypothesis, `confirmed` means a one-variable
replay removed the observed failure, and `rejected` means that change did not
fix it.

## API

The console uses two read-only endpoints:

```text
GET /v1/incidents
GET /v1/incidents/{diagnosis_id}
```

The list endpoint accepts `status`, `category`, `limit`, and `offset`. Status is
restricted to the diagnosis lifecycle values, limits are bounded from 1 to
500, and invalid filters return HTTP 422.

Both public response shapes have Draft 2020-12 contracts:

- `schemas/incident-list.schema.json`
- `schemas/incident-detail.schema.json`

## Content boundary

The incident list contains identifiers, categories, counts, timestamps, and
replay state. The detail response includes claim verdict metadata, evidence
chunk identifiers, document versions, hashes, and private object references.
It does not return CUAD question text, answer text, evidence passages, or claim
explanations.

This is defense in depth for the UI. The existing full-content APIs and object
store remain governed by the Stage 0 privacy contract. Authentication and
external exposure are not part of Stage 8, so this dashboard must remain bound
to localhost until an authenticated production gateway is added.

## Validate

```bash
RAGPROOF_RUN_STORAGE_INTEGRATION=1 .venv/bin/python -m unittest \
  tests.test_incident_dashboard_integration -v
```

The integration suite uses an observed generation failure over the real pinned
CUAD v1 fixture. It confirms list and detail contracts, filters, replay and
regression linkage, content exclusion, dashboard assets, invalid input, and
not-found behavior.

Stage 8 intentionally does not add incident mutation, human adjudication,
authentication, outbound alerts, or CI enforcement. Those need separate
contracts rather than being hidden inside a read-only inspection surface.
