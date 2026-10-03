# Stage 6: controlled differential replay

Stage 6 converts a suspected root-cause hypothesis into an experiment. It
reconstructs the pipeline configuration recorded on the original trace, runs
an unchanged baseline, changes exactly one diagnosed variable, and compares
the resulting answer, claim verdicts, citation alignment, and pipeline
fingerprints.

No hosted model, paid API, API key, or generated evaluation dataset is used.
Both replay arms run against the pinned real CUAD v1 contracts and unchanged
human annotation. Controlled component failures remain explicitly labelled test
conditions.

## Experiment protocol

```text
diagnosis.created
  -> reconstruct component modes from the original canonical trace
  -> transition diagnosis: suspected -> replaying
  -> run unchanged baseline as a new root trace
  -> change exactly one diagnosed component
  -> run candidate as a separate new root trace
  -> ingest, verify, and compare both arms
  -> persist replay and transition diagnosis
  -> publish replay.completed
```

The baseline and candidate have separate OpenTelemetry trace IDs. They are
correlated through `extensions.replay.replay_id` rather than inheriting the
worker's trace context. This avoids storage collisions while keeping the
experiment queryable.

## Single-variable controls

The Stage 6 CUAD provider maps each diagnosis to one configuration field:

| Diagnosis | Changed field | Candidate value |
| --- | --- | --- |
| `PARSING_FAILURE` | parser mode | `healthy` |
| `CHUNKING_FAILURE` | chunker mode | `healthy` |
| `RETRIEVAL_FAILURE` | retriever mode | `healthy` |
| `RERANKING_FAILURE` | reranker mode | `healthy` |
| `GENERATION_FAILURE` | generator mode | `healthy` |
| `CITATION_FAILURE` | citation mode | `healthy` |
| `STALE_KNOWLEDGE` | corpus mode | `current` |

Before running, the engine compares every behavior-affecting field and refuses
the replay unless exactly the declared field differs. It also refuses automatic
replay for `UNDETERMINED` diagnoses.

## Outcome rules

`CONFIRMED` requires both conditions:

1. The unchanged baseline reproduces the original pipeline fingerprint,
   response hash, claim verdicts, and citation-alignment state.
2. Changing only the diagnosed component removes the failure. For most
   categories all candidate claims must become `SUPPORTED`; for citation
   failures the citation must change from non-supporting to supporting.

`REJECTED` means the baseline reproduced but the one-variable repair did not
remove the failure. `NEEDS_REVIEW` means the baseline itself did not reproduce,
so the experiment cannot establish causality.

Diagnosis lifecycle transitions are stored in an append-only history table.
The original content-addressed diagnosis artifact remains immutable while the
queryable current status moves through `suspected`, `replaying`, and one of
`confirmed`, `rejected`, or `needs_review`.

## Real-data results

All seven single-fault CUAD scenarios are confirmed by their one-variable
repair. Each baseline reproduces the original and every candidate uses a
different pipeline fingerprint. Citation repair is evaluated separately from
answer correctness, allowing an unchanged correct answer with a repaired
citation to confirm the citation diagnosis.

A controlled multi-fault run combines retrieval exclusion and numeric
generation corruption over the same real CUAD evidence. Repairing retrieval
alone exposes the remaining generation contradiction, so the replay is
correctly `REJECTED`. This verifies that the engine does not equate “a component
changed” with “the incident is fixed.”

These experiments validate causal wiring in the controlled laboratory. The
current replay provider intentionally supports only CUAD laboratory traces.
Production use will require customer-specific replay adapters capable of
reconstructing their immutable corpus, index, prompt, and model configuration.

## Interfaces

```text
POST /v1/replays/diagnoses/{diagnosis_id}  execute or return a replay
GET  /v1/replays/{replay_id}              fetch the persisted result
```

The `ragproof.replay` span records identifiers, engine version, changed
component, and outcome only. Answer, query, contract, and evidence content are
not placed in telemetry.

## Run

```bash
docker compose -f infrastructure/docker-compose.lab.yml up -d
RAGPROOF_RUN_STORAGE_INTEGRATION=1 \
  .venv/bin/python -m unittest tests.test_replay_integration -v

.venv/bin/ragproof-replay-worker --once
```
