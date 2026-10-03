# Stage 4: claim-to-evidence verification

Stage 4 evaluates what the answer says against the evidence the model actually
received. It does not search the entire corpus after the fact and present that
as grounding. This distinction prevents evidence that was never in the prompt
from being used to excuse an unsupported generation.

No hosted LLM, generated evaluation dataset, paid API, or API key is used in
this stage. The ground truth remains the real CUAD v1 subset and its unchanged,
lawyer-supervised answer annotations.

## Processing flow

```text
trace.ingested Redis event
  -> load response and included context from content storage
  -> conservative sentence-level claim extraction
  -> direct support / numeric contradiction / partial overlap alignment
  -> document-version freshness check
  -> persist verification, claims, and claim-evidence edges
  -> acknowledge the Redis message
```

The verifier emits a `ragproof.evaluate` OpenTelemetry span containing only the
response identifier, evaluator version, status, claim count, and verdicts.
Claim or evidence text is never placed in telemetry attributes.

## Verdict behavior

- `SUPPORTED`: the normalized claim is directly present in a captured context
  chunk.
- `STALE_EVIDENCE`: direct support exists, but the consumed document version is
  superseded by the trace's recorded current version.
- `CONTRADICTED`: a closely aligned context statement preserves the claim's
  non-numeric structure but contains conflicting numeric terms.
- `PARTIALLY_SUPPORTED`: most claim terms align with one statement but direct
  support is incomplete.
- `UNSUPPORTED`: captured context exists but no statement directly supports the
  claim.
- `INSUFFICIENT_CONTEXT`: an answer claim was captured but usable context was
  not.

When the response itself was not retained, the system does not fabricate claim
text to satisfy the output schema. It persists a separate
`blocked_content_unavailable` job result with the reason. This is the expected
behavior for metadata-only and redacted inputs.

## Persistence and lineage

Completed verification JSON is validated against
`schemas/claim-verification.schema.json` and stored as a content-addressed
object. PostgreSQL stores its hash/reference and queryable relational fields:

- evaluator name and immutable version;
- claim ID, ordinal, text hash, verdict, and confidence;
- exact trace/chunk evidence edge, relationship, document version, and
  freshness.

Raw claim text is not duplicated into relational tables. Reverse document
impact now returns both affected answers and affected verified claims.

## Real-data evidence

The automated proof includes:

- 96 unchanged human answer annotations across the three pinned CUAD contracts;
  every extracted claim remains directly locatable in its source contract;
- a complete real-trace `SUPPORTED` case;
- a labelled controlled generation fault that changes the real annotated
  notice period from fifteen to thirty days and must be `CONTRADICTED`;
- a real annotation retrieved from a deliberately superseded version and
  classified `STALE_EVIDENCE`;
- a metadata-only trace that must be blocked without invented text;
- actual Redis consumer-group processing, PostgreSQL persistence, MinIO
  retrieval, HTTP evaluation, and JSON Schema validation.

This does **not** establish general semantic-entailment accuracy. The 96 positive
annotations test preservation and exact-support alignment and contain no
balanced negative class. Precision/recall claims would therefore be misleading.
The deterministic lexical-numeric verifier is an auditable MVP baseline behind
a replaceable evaluator boundary; a later model-backed verifier must be tested
on independently labelled real data before production claims are made.

## Run

```bash
docker compose -f infrastructure/docker-compose.lab.yml up -d
RAGPROOF_RUN_STORAGE_INTEGRATION=1 \
  .venv/bin/python -m unittest tests.test_verification_integration -v

# Process one queued trace, or omit --once to keep consuming.
.venv/bin/ragproof-verify-worker --once

# Explicit API evaluation/readback.
.venv/bin/uvicorn ragproof_store.api:create_app --factory --port 8080
```
