# Stage 5: evidence-backed root-cause diagnosis

Stage 5 converts claim verdicts and recorded pipeline observations into ranked
failure hypotheses. It follows the failure taxonomy's earliest-causal-stage
precedence and never marks a diagnosis confirmed from heuristics alone.

No hosted model, paid API, API key, or generated evaluation dataset is used.
The controlled laboratory continues to use the real, unchanged CUAD contract
text and human answer annotation. Faults are injected into pipeline components
and explicitly labelled only for evaluation.

## Evidence flow

```text
verification.completed
  -> load canonical trace + claim verification + citation lineage
  -> examine source/parse/chunk/retrieval/context evidence probes
  -> apply earliest-causal-stage precedence
  -> consider claim contradiction, staleness, and citation alignment
  -> rank hypotheses and recommend one controlled experiment
  -> persist diagnosis and publish diagnosis.created
```

The CUAD laboratory records boolean intermediate-stage probes against the hash
of the existing human annotation. These probes say whether that exact evidence
survived each pipeline stage. They do not include the injected fault category,
and the classifier does not read `ragproof.scenario.name`. A test deliberately
changes that label to `healthy` and confirms the evidence still produces the
correct retrieval diagnosis.

Production traces without optional human labels can still diagnose cases whose
captured evidence is sufficient, such as an answer claim contradicted by its
actual model context. When the evidence cannot isolate a component, the result
is `UNDETERMINED` with `needs_review`, not a confident guess.

## Precedence

When the human-labelled evidence is available, the first proven break wins:

1. source contains evidence, parsed representation does not: `PARSING_FAILURE`;
2. parsing retains it, no complete chunk does: `CHUNKING_FAILURE`;
3. a chunk retains it, retrieval omits it: `RETRIEVAL_FAILURE`;
4. retrieval returns it, context selection omits it: `RERANKING_FAILURE`;
5. current evidence reaches context but the claim conflicts: `GENERATION_FAILURE`;
6. the answer is grounded but its recorded citation target is not:
   `CITATION_FAILURE`;
7. support comes from a superseded version: `STALE_KNOWLEDGE`.

Every non-undetermined result begins with lifecycle status `suspected`.
`confirmed` is reserved for a one-variable replay or explicit human decision.

## Citation lineage correction

Stage 5 extends the canonical trace with the exact cited chunk identity, hash,
document version, and policy-controlled content/reference. Citation targets are
persisted independently from retrieval candidates because an incorrect
citation can point to a chunk that was never retrieved. This allows RAGProof to
prove a citation failure instead of merely noticing that the answer is
supported somewhere else.

## Persistence and interfaces

PostgreSQL stores diagnoses, linked claim IDs, and ranked hypotheses. The full
schema-valid diagnosis is content-addressed in MinIO. Redis connects the Stage
4 and Stage 5 workers using `verification.completed` and `diagnosis.created`
events.

```text
POST /v1/diagnoses/{response_id}  create or return an idempotent diagnosis
GET  /v1/diagnoses/{response_id}  fetch the persisted diagnosis
```

The `ragproof.diagnose` OpenTelemetry span records only response ID, diagnoser
version, lifecycle status, and category—never contract, answer, or evidence
text.

## Real-data result and limitation

The seven controlled CUAD scenarios place their injected category at rank 1:
parsing, chunking, retrieval, reranking, generation, citation, and stale
knowledge. The observed top-two hit rate is therefore 7/7 for this small
laboratory, exceeding the MVP's 80% entry target. The healthy trace is returned
as `UNDETERMINED` rather than being assigned a false failure.

This result proves deterministic taxonomy wiring on the controlled laboratory;
it is not a general production accuracy claim. Seven scenarios over one primary
question are far too small for that. Wider, independently labelled real-world
incident traces will be required before publishing diagnostic precision or
recall.

## Run

```bash
docker compose -f infrastructure/docker-compose.lab.yml up -d
RAGPROOF_RUN_STORAGE_INTEGRATION=1 \
  .venv/bin/python -m unittest tests.test_diagnosis_integration -v

.venv/bin/ragproof-verify-worker --once
.venv/bin/ragproof-diagnose-worker --once
```
