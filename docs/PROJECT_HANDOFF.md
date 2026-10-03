# RAGProof — complete project and new-chat handoff

Snapshot date: **2026-10-03**. This document describes the source snapshot prepared for the first GitHub publication, not a claim of production readiness. Read [REMAINING_WORK.md](REMAINING_WORK.md) immediately after this document.

## 1. Executive summary

RAGProof is a **vendor-neutral RAG reliability and debugging layer**. It records the evidence used by a retrieval-augmented generation application, checks whether the answer's claims are supported by that evidence, identifies likely pipeline failures, tests a controlled repair, and turns confirmed failures into regression checks.

The intended product complements Langfuse, LangSmith, and OpenTelemetry rather than replacing their tracing products. The proposed differentiator is the linked workflow **claim → evidence → suspected cause → controlled replay → affected answers → regression gate**. This is a product hypothesis, not an independently established claim of market novelty or defensibility.

The repository contains a substantial local, real-data prototype: a deterministic CUAD RAG laboratory, instrumentation, storage, claim verification, diagnosis, replay, impact analysis, a dark incident dashboard, a CI quality gate, and an external OTLP receiver. Additional security, delivery, packaging, backup, and vendor-import work is present but not fully validated. In particular, the advertised `ragproof-admin` command has no implementation module in this snapshot.

**Do not describe the repository as a finished SaaS, a general hallucination detector, or a fully tested production system.** A supported claim is supported by captured evidence under a specific verifier; it is not necessarily objectively true.

## 2. User intent and non-negotiable requirements

The initial idea was a personalized news service. Discussion shifted to an SDK and observability-style product for checking and debugging RAG systems. The selected project is RAGProof; the news idea is not part of this codebase.

The user wants an industry-relevant, technically credible portfolio/product project, not a superficial dashboard around an LLM. The agreed development direction is:

- Use actual datasets and original annotations. Do not invent passages, questions, answers, performance numbers, vendor traces, or customer incidents and present them as real evaluation evidence.
- Controlled failure injection into an actual pipeline over real source data is permitted by the existing laboratory design, but must be labelled as such. It is not a naturally occurring production incident dataset.
- Tell the user when API access or another decision is required. Never ask for secrets in chat or commit them.
- The user has **neither a real Langfuse nor a real LangSmith project available yet** and selected “finish local setup.” Do not claim live vendor validation happened.
- The user requested a high-quality dark UI with restrained backdrop, hover, and motion effects, not a generic blue AI dashboard. Preserve useful evidence presentation and accessibility.
- Development was initially local. The user has now explicitly authorized publication of the current code and these documents to their GitHub repository.

## 3. Repository and environment

- Repository: <https://github.com/yatharth7115/ragproof>.
- Local project directory for this development session: `/home/austin007/ragproof`.
- The surrounding desktop task can start in `/home/austin007/tenantowner`; that is **not** this project's root. Check the directory before running commands.
- Python package/distribution: `ragproof-mvp`, version `0.1.0`, Python `>=3.10`.
- Existing virtual environment: `.venv/`; not included in Git.
- API/dashboard default: `http://127.0.0.1:8080/dashboard`.
- At the start of publication the local repository had no commits or remote, and the target GitHub repository was empty. Earlier development stages therefore do not have separate historical Git commits. This publication is a consolidated snapshot, not reconstructed stage history.
- The repository contains third-party data attribution, but **no project source-code LICENSE has been chosen**. Public visibility is not the same as an open-source license grant.

## 4. Architectural model

```text
Real RAG application / deterministic CUAD laboratory
  ├─ direct canonical trace client
  ├─ complete-tree OpenTelemetry export → OTLP/HTTP receiver
  └─ explicit vendor mapping → Langfuse/LangSmith import code (not live-validated)
                         ↓
              Canonical trace validation + privacy handling
                         ↓
       PostgreSQL/pgvector + S3-compatible content storage
                         ↓
         SQL outbox → Redis Streams → background workers
                         ↓
       claim verification → diagnosis → controlled replay
                                            ↓
                        confirmed cases → regression quality gate
                         ↓
       lineage / document impact / incident console / reports
```

The current application accepts OTLP directly; a separately operated OpenTelemetry Collector is not a required local component. The architecture illustration was a roadmap, not evidence that every illustrated box was complete.

PostgreSQL holds records and relationships. MinIO provides the local S3-compatible content store. Redis carries processing events. `pgvector` stores laboratory embeddings; the current embedding implementation is deterministic hashed features, not a production semantic embedding model. Evidence lineage is represented with relational tables and joins, **not a separate graph database or a general GraphRAG engine**.

## 5. Real dataset and evaluation boundaries

The tracked fixture is `fixtures/cuad_eval_subset.json`, drawn from **Contract Understanding Atticus Dataset (CUAD) v1**, published by The Atticus Project under **CC BY 4.0**. See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

Source archive: <https://raw.githubusercontent.com/TheAtticusProject/cuad/main/data.zip>.

Pinned archive SHA-256:

```text
f8161d18bea4e9c05e78fa6dda61c19c846fb8087ea969c172753bc2f45b999a
```

The fixture contains three contracts, 123 question records, and 96 original answer annotations. Of those questions, 45 have answers in this subset. Source contract text and source annotations are unchanged. `scripts/build_cuad_fixture.py` selects the subset deterministically and checks the archive hash. Downloaded source archives stay under ignored `data/`.

Selected contracts:

1. CENTRACKINTERNATIONALINC_10_29_1999-EX-10.3-WEB SITE HOSTING AGREEMENT.
2. LIMEENERGYCO_09_09_1999-EX-10-DISTRIBUTOR AGREEMENT.
3. FTENETWORKS,INC_02_18_2016-EX-99.4-STRATEGIC ALLIANCE AGREEMENT.

The end-to-end failure laboratory primarily uses the first contract's **“Notice Period To Terminate Renewal”** question. Seven fault categories around that one query are **not seven independent questions** and do not establish broad-domain performance.

The local generator extracts from retrieved evidence; it does not call a live LLM. A deliberate generation fault changes a number in an answer. Stale evidence is currently exercised through version/freshness metadata, not a corpus of independently collected historical document revisions. These are transparent controlled test conditions.

`ragproof-evaluate` separately evaluates unchanged annotated answers against their original contract. On this publication date it preserved **96/96 annotations**, produced **109 supported atomic claims**, and found **zero invalid source offsets**. This is a **positive support-preservation check**, not hallucination precision, recall, specificity, or semantic accuracy. Do not advertise it as “100% hallucination detection.”

Files in `examples/` illustrate contracts and API shapes. They are not an independent benchmark or a record of live customer incidents. In particular, example identifiers/hashes must not be cited as observed production evidence.

## 6. Stage-by-stage implementation history

### Stage 0 — contracts and scope

Files: `docs/product-contract.md`, `docs/canonical-trace.md`, `docs/failure-taxonomy.md`, `docs/privacy-security.md`, `schemas/`, `examples/`.

Defined the canonical trace, evidence identity, failure categories, privacy modes, and MVP boundary before the pipeline implementation. JSON Schema describes the interchange formats for traces, verification, diagnosis, replay, impact, regressions, incidents, and quality-gate reports.

The essential design decision is to capture enough lineage to distinguish missing evidence from faulty generation. A generated answer alone cannot reliably identify whether parsing, retrieval, reranking, or generation failed. Design/privacy documents describe intended guarantees; where they exceed implementation, this handoff and the remaining-work document call that out.

### Stage 1 — controlled RAG failure laboratory

Files: `src/ragproof_lab/`, `fixtures/`, `scripts/build_cuad_fixture.py`, `docs/stage-1-laboratory.md`.

Implemented parsing, chunking, deterministic embeddings, retrieval, reranking, prompt construction, and extractive answer generation over the pinned real CUAD fixture. Component interfaces allow controlled behavior changes while source data stays identifiable.

The healthy scenario is the baseline. Failure scenarios exercise:

- Parsing loss: remove the relevant annotated span from parser output.
- Chunking loss: split relevant evidence so the required support is not preserved usefully.
- Retrieval failure: exclude the needed evidence from candidates.
- Reranking failure: demote relevant evidence out of the selected context.
- Generation failure: produce a deliberately inconsistent numeric answer.
- Citation failure: attach an incorrect evidence reference.
- Stale evidence: retain an obsolete-version marker in the evidence path.

The laboratory can run without a model API key. Its value is reproducibility and causal testing, not proof that these deterministic components match a production LLM or retriever.

### Stage 2 — OpenTelemetry capture and canonical normalization

Files: `src/ragproof_otel/`, `docs/stage-2-opentelemetry.md`.

Instrumented the request and pipeline stages, captured spans, and mapped them into a canonical RAG trace. The intended tree contains `rag.request` and component spans for document parsing, chunking, embedding creation, retrieval, reranking, prompt construction, and generation.

Traces associate document/version/chunk identities with candidates, ranks, selected context, answer content hashes, and citations. They preserve evidence needed downstream instead of relying only on latency and token counters.

Capture modes are `metadata`, `full`, and `redacted`. Full capture requires explicit consent in relevant entry points. Metadata-only capture must not invent missing text or claim content-based verification was performed. The current redacted mode is a marker-based treatment, **not a validated PII detector**.

Later changes added a complete-tree span processor so export does not accidentally split the canonicalization input across unrelated batches. Its bounds, synchronous export behavior, and production latency still require load testing.

### Stage 3 — storage and evidence lineage

Files: `src/ragproof_store/repository.py`, `schema.sql`, `object_store.py`, `config.py`, `events.py`, `infrastructure/`, `docs/stage-3-storage-lineage.md`.

Added PostgreSQL persistence, `pgvector` embedding columns/indexes, hash-addressed S3-compatible artifacts, and Redis event publication. The repository ingests canonical traces, records their pipeline relationships, and exposes answer lineage and document reverse lookups.

Relationships connect tenant/project → trace → query/response, document → version → chunk, retrieval candidate → selected context, and answer → citation. Later stages add claim evidence, diagnosis, replay, regression, and gate records.

Recent ingestion hardening deep-copies/sanitizes traces, checks content references, and serializes same-trace operations with an advisory lock. Authentication and complete tenant-isolation assurance were **not** part of the original storage-stage validation; new controls need their own tests.

### Stage 4 — claim/evidence verification

Files: `src/ragproof_verifier/`, `schemas/claim-verification.schema.json`, `docs/stage-4-claim-verification.md`.

Extracts atomic claims from captured answer text, compares them to captured evidence, and persists verdicts plus evidence alignment. It can run explicitly through the API or asynchronously after ingestion.

Current verifier version: `lexical-numeric-0.1.1`. It uses lexical/numeric rules, not a fully calibrated semantic entailment model or an independently reliable LLM judge.

Verdicts include `SUPPORTED`, `PARTIALLY_SUPPORTED`, `UNSUPPORTED`, `CONTRADICTED`, `INSUFFICIENT_CONTEXT`, and `STALE_EVIDENCE`. Missing content is a limitation to report, not a reason to manufacture a verdict. Metadata-only traces do not contain enough information for full claim verification.

Lexical overlap and matching numbers can miss semantic contradictions; paraphrases can be misclassified. Groundedness in a source does not establish source truth. The positive CUAD evaluation above addresses only one narrow failure mode: losing support for unchanged annotations.

### Stage 5 — evidence-based root-cause diagnosis

Files: `src/ragproof_diagnosis/`, `schemas/diagnosis.schema.json`, `docs/stage-5-root-cause-diagnosis.md`.

Ranks hypotheses using recorded pipeline evidence, verification results, citations, and optional laboratory ground-truth probes. Preserves the diagnostic reason and component/category associations.

The classifier does not silently turn correlation into causation. A hypothesis remains suspected until replay or human review resolves it. Healthy/ambiguous cases can produce `UNDETERMINED`/`needs_review`; they must not be marketed as confirmed incidents or counted as verifier accuracy failures without an explicit metric definition.

Lab ground-truth extensions are available because CUAD has human annotations. A customer trace may not provide such information. The external path must be allowed to abstain instead of assuming laboratory probes exist.

### Stage 6 — differential replay

Files: `src/ragproof_replay/`, `schemas/replay-result.schema.json`, `docs/stage-6-differential-replay.md`.

Re-executes a clean baseline, changes one diagnosed component variable, compares outcomes, and records a confirmed, rejected, or needs-review result. Repeated processing is designed to avoid creating uncontrolled duplicate repairs.

Controlled replay is implemented for the deterministic CUAD laboratory. It is **not** an executor for arbitrary customer code, hosted models, or vendor applications. Multiple simultaneous faults are not automatically repaired; the one-variable constraint is intentional.

Replays record evidence for the conclusion rather than simply overwriting the original trace. Later worker guards skip replay/quality-gate-produced traces where necessary to prevent recursive processing loops.

### Stage 7 — document impact and regression cases

Files: `src/ragproof_impact/`, `schemas/change-impact.schema.json`, `schemas/regression-case.schema.json`, `docs/stage-7-change-impact-regressions.md`.

Follows direct document-version/chunk relationships through retrieval, context, citations, and claims to identify potentially affected answers. A changed source can affect many earlier responses; the analyzer reports that linkage rather than assuming every linked answer is now wrong.

Current impact is direct relational lineage. Without a complete content/version manifest, actual `content_change` can remain unknown. This is not unrestricted graph inference or proven transitive semantic blast-radius analysis.

Only confirmed replays are promoted into immutable regression cases. Cases preserve the query identity, expected verification/citation behavior, provenance, and replay reference. Suspected diagnoses are not automatically ground truth.

### Stage 8 — incident console and visual redesign

Files: `src/ragproof_store/static/incident-dashboard.html`, `.css`, `.js`; `docs/stage-8-incident-console.md`.

Implemented a server-served, vanilla HTML/CSS/JavaScript dashboard. There is no separate React/Next.js application or npm build requirement. It reads incident list/detail APIs and exposes category/status filters, pagination, summary counts, refresh, and an investigation drawer with supporting evidence.

The current visual direction uses charcoal backgrounds, light typography, emerald/amber signals, ambient effects, and hover/motion treatments. The drawer has dialog semantics and keyboard-related handling. The interface explicitly labels confirmation share as **not an accuracy or health score**.

The persisted page still says CUAD/local workspace and Stage 08. It is primarily an incident view; a complete login/onboarding flow, separate trace explorer, quality-gate workspace, and live integration setup pages are **not implemented in this static frontend**. New backend endpoints do not automatically mean those UI screens exist. Recheck responsive layout and accessibility after future changes.

### Stage 9 — CI quality gate

Files: `src/ragproof_gate/`, `schemas/quality-gate-result.schema.json`, `.github/workflows/ragproof-quality-gate.yml`, `docs/stage-9-ci-quality-gate.md`.

Selects the newest confirmed regression case per `(query_hash, category)` to avoid weighting repeated executions of the same case. Runs a declared candidate, compares verdict/citation expectations, persists an immutable report and case outcomes, and returns a process-level release decision.

Default policy: at least one case and a pass rate of 1.0; required categories may also be specified. Empty coverage fails closed. CLI exit code `0` means pass, `1` means fail.

The current workflow brings up the local data plane, runs integration tests, backfills confirmed cases, and requires all seven laboratory failure categories. This is useful end-to-end wiring, but generating the regression inventory using the same candidate code during CI is not a sufficient independent release benchmark. A separately reviewed frozen corpus remains necessary.

The current candidate path is tied to the primary CUAD query. Seven category passes should not be described as multi-query product coverage.

### Stage 10 — external OTLP ingestion

Files: `src/ragproof_store/otlp.py`, `src/ragproof_otel/otlp_adapter.py`, `processor.py`, `scripts/send_real_cuad_otlp.py`, `docs/stage-10-external-otel-integration.md`.

Adds `POST /v1/otlp/traces` with OTLP protobuf/JSON and optional gzip. Validates a complete RAG span tree, normalizes trace IDs and relationships, and rejects incomplete/malformed lineage rather than guessing missing evidence. Authorization is passed into normalization before writes in the recent changes.

The local smoke sender uses real CUAD pipeline executions through an actual OTLP HTTP path. This demonstrates a separate transport boundary; it is not an independent customer's application or live Langfuse/LangSmith certification.

Generic OpenTelemetry spans without the required RAG fields are not plug-and-play. Handling arbitrary out-of-order/batched/incomplete customer traces remains future work.

### Subsequent completion pass — present but unfinished

The previous broad “finish everything” implementation pass was interrupted. The source contains the following additions; treat them as **work to validate**, not a completed release:

- `security.py`: project-scoped hashed API keys, reader/writer/admin roles, signed browser sessions, origin checks; token-mode middleware and resource ownership checks in `api.py`.
- `privacy.py`: capture-mode sanitization and content-reference restrictions. Unknown extensions are restricted; this needs reconciliation with the vendor-neutral contract.
- `migrations.py` and `migration_sql/0002_delivery.sql`: numbered migrations, checksums/drift checks, migration locking, outbox and webhook-delivery records.
- `delivery.py`: SQL outbox dispatch to Redis, plus optional signed HTTPS failed-gate webhook delivery with retries.
- `consumer.py`: pending-event recovery, bounded retries/dead letters, shared worker handling.
- `ragproof_integrations/`: canonical HTTP client; declared field/JSON-pointer mappings; bounded Langfuse observation import and LangSmith SDK read adapter. No real vendor credentials or live traces have validated these paths.
- `ragproof_resources/`, `setup.py`, `MANIFEST.in`: wheel resource packaging so schemas/fixtures are not dependent solely on the source checkout.
- `evaluate.py`: real annotation support-preservation evaluation; optional pinned full-archive input.
- `backup.py`: database/content/queue backup and empty-target restore tooling, checksums and explicit apply/quiescence controls. A successful restore drill is not recorded.
- `Dockerfile`, `docker-compose.app.yml`, `constraints.txt`, `.env.example`, `Makefile`: self-hosted setup and constrained installation scaffolding.

Known concrete defect: `pyproject.toml` registers `ragproof-admin = ragproof_store.admin:main`, and `.env.example` refers to it, but **`src/ragproof_store/admin.py` does not exist**. Do not tell the user to run this command successfully before implementing and testing it.

## 7. Source map and ownership boundaries

| Directory/file | Responsibility |
| --- | --- |
| `src/ragproof_lab` | Deterministic real-data pipeline and fault scenarios |
| `src/ragproof_otel` | Span capture, complete-tree processing, canonical/OTLP mapping |
| `src/ragproof_store` | API, SQL repository, content storage, security, queues, migrations, backups |
| `src/ragproof_verifier` | Claim extraction, evidence checks, annotation evaluation |
| `src/ragproof_diagnosis` | Hypothesis ranking and diagnosis worker |
| `src/ragproof_replay` | Controlled baseline/repair execution |
| `src/ragproof_impact` | Direct change lineage and confirmed regression promotion |
| `src/ragproof_gate` | Candidate evaluation and fail-closed release policy |
| `src/ragproof_integrations` | Canonical client and explicit vendor import mappings |
| `src/ragproof_resources` | Packaged schema and fixture access |
| `schemas`, `examples` | Machine-readable contracts and illustrative payloads |
| `tests` | Unit, protocol, contract, and infrastructure integration tests |
| `infrastructure` | Local data-plane and application-stack Compose definitions |
| `docs` | Stage design notes plus these cross-stage handoff/backlog documents |

The repository is still compact enough to work as one Python project. Do not split it into many services/packages merely to imitate the architecture drawing.

## 8. Persistence and delivery semantics

SQL tables cover tenants/projects/audit events; traces/spans/queries/responses; documents/versions/chunks; retrieval candidates/citations; verifications/claims/claim evidence; diagnoses/hypotheses/status history; replays; document changes/impact reports; regression cases; gate runs/case outcomes. Migration bookkeeping uses `ragproof_schema_migrations`; delivery adds `ragproof_event_outbox` and `ragproof_webhook_deliveries`.

Content objects are hash-addressed and referenced from records. Both SQL and object storage matter during backup and restore. Redis alone is not the source of truth for analysis records.

Outbox publication is **at least once**: publishing to Redis and marking a SQL event delivered cannot be treated as one atomic cross-system transaction. A crash between them can duplicate delivery. Consumers and side effects must therefore be idempotent. New recovery/dead-letter code requires failure-injection tests before reliable delivery is claimed.

Migrations checksum `schema.sql` as `0001_initial`; later changes should be new numbered migrations, not edits to already-applied SQL. The transition from older manually initialized databases also needs validation. Do not delete a user's database to bypass a migration mismatch.

## 9. API surface

The API factory is `ragproof_store.api:create_app`. Read `api.py` and the running `/docs` for exact request bodies and current behavior.

| Routes | Purpose |
| --- | --- |
| `GET /health/live`, `/health/ready` | Process/data-plane health |
| `GET /auth/status`, `POST/DELETE /auth/session` | Authentication state/session operations |
| `GET /dashboard`, `/dashboard-assets/*` | Static incident UI |
| `GET /v1/workspace` | Workspace summary |
| `POST/GET /v1/traces`, `GET /v1/traces/{trace_id}` | Ingest/list/read canonical traces |
| `POST /v1/otlp/traces` | Strict OTLP ingestion |
| `GET /v1/lineage/answers/{response_id}` | Answer-to-source lineage |
| `POST/GET /v1/verifications/{response_id}` | Run/read claim verification |
| `POST/GET /v1/diagnoses/{response_id}` | Run/read diagnosis |
| `POST /v1/replays/diagnoses/{diagnosis_id}`, `GET /v1/replays/{replay_id}` | Controlled replay |
| `GET /v1/impact/documents/{document_id}` | Direct document impact lookup |
| `POST /v1/impact/documents/{document_id}/analyze`, `GET /v1/impact/reports/{impact_id}` | Persist/read impact report |
| `POST /v1/regressions/replays/{replay_id}`, `GET /v1/regressions/{case_id}` | Promote/read confirmed regression |
| `POST/GET /v1/quality-gates`, `GET /v1/quality-gates/{gate_run_id}` | Execute/list/read gate reports |
| `GET /v1/incidents`, `GET /v1/incidents/{diagnosis_id}` | Incident list/detail |

Tenant/project identity should come from authenticated scope, not be trusted because a client supplied it. Current token middleware implements checks, but exhaustive cross-tenant tests are still required. Local mode bypasses production-style isolation and must remain loopback-only.

## 10. Local operation and configuration

### Fresh source checkout

```bash
cd /path/to/ragproof
python3 -m venv .venv
.venv/bin/python -m pip install -c constraints.txt -e '.[dev,api,otlp,storage,integrations]'
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m ragproof_verifier.evaluate
```

Constraints reflect the current development environment; fresh-machine and isolated-wheel reproducibility still need validation. LangSmith is a separate optional extra, not required for the local workflow.

### Local data plane and API

```bash
docker compose -f infrastructure/docker-compose.lab.yml up -d --wait
.venv/bin/python -m ragproof_store.migrations --check
.venv/bin/python -m uvicorn ragproof_store.api:create_app --factory --host 127.0.0.1 --port 8080
```

The migration check is read-only and may report pending migrations. Inspect that result before deliberately applying migrations with `python -m ragproof_store.migrations`. Do not assume a previously running database already matches the current source.

Local data-plane defaults: PostgreSQL `127.0.0.1:55432`, Redis `127.0.0.1:56379`, MinIO API `127.0.0.1:59000`, MinIO console `127.0.0.1:59001`. A browser at port 59001 is the object-store console, not RAGProof's incident UI.

`docker-compose.lab.yml` uses explicitly local development credentials. They are not deployment secrets and must not be reused publicly. `docker-compose.app.yml` is a separate authenticated-stack scaffold with internal service hostnames and a separate database name; do not mix its DSN with the lab DSN.

### Real trace and workers

```bash
.venv/bin/ragproof-lab --list
.venv/bin/ragproof-capture --scenario healthy --capture-mode full
.venv/bin/python scripts/send_real_cuad_otlp.py --endpoint http://127.0.0.1:8080/v1/otlp/traces
.venv/bin/ragproof-verify-worker --once
.venv/bin/ragproof-diagnose-worker --once
.venv/bin/ragproof-replay-worker --once
.venv/bin/ragproof-regression-worker --once
```

Capture produces a trace; merely printing it does not necessarily persist it. Use the sender or an ingestion client/API for persistence. Consult `--help` for consent/capture settings; do not assume metadata-only exports contain verifiable answer content. Long-running workers and the outbox dispatcher need supervision for unattended operation.

Run infrastructure tests only against the disposable local/test data plane, never an important customer database:

```bash
RAGPROOF_RUN_STORAGE_INTEGRATION=1 .venv/bin/python -m unittest discover -s tests -v
.venv/bin/ragproof-regression-worker --backfill-confirmed
.venv/bin/ragproof-gate --minimum-cases 1 --minimum-pass-rate 1.0
```

An empty regression database should fail the gate. Do not insert fabricated successes to make the dashboard or gate look populated.

### Configuration inventory

Storage uses `RAGPROOF_POSTGRES_DSN`, `RAGPROOF_REDIS_URL`, `RAGPROOF_S3_ENDPOINT_URL`, `RAGPROOF_S3_ACCESS_KEY`, `RAGPROOF_S3_SECRET_KEY`, `RAGPROOF_S3_BUCKET`, and `RAGPROOF_S3_REGION`.

Authentication uses `RAGPROOF_AUTH_MODE` (`local` or `token`), `RAGPROOF_API_KEYS_JSON`, `RAGPROOF_SESSION_SECRET`, and `RAGPROOF_COOKIE_SECURE`. Token records contain `key_hash`, `tenant_id`, `project_id`, and `role`; stored key hashes are SHA-256 values, not raw keys. Session signatures use a configured secret, and sessions expire after eight hours. Secure cookies require HTTPS; disabling secure cookies is acceptable only for deliberate local HTTP testing.

The canonical client supports `RAGPROOF_ENDPOINT` and `RAGPROOF_API_KEY`. Vendor imports require actual vendor access and an explicit mapping: Langfuse uses `LANGFUSE_BASE_URL`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`; LangSmith uses `LANGSMITH_API_KEY` and optionally `LANGSMITH_ENDPOINT`.

Optional alert delivery uses `RAGPROOF_ALERT_WEBHOOK_URL`, `RAGPROOF_ALERT_WEBHOOK_SECRET`, `RAGPROOF_ALERT_TENANT_ID`, and `RAGPROOF_ALERT_PROJECT_ID`. Do not enable outbound notifications without an intentionally chosen destination. This path has not been live-validated.

## 11. Verification evidence for this publication

Checks run on **2026-10-03**, in the existing Python 3.10 development environment:

| Check | Observed result | What it does not establish |
| --- | --- | --- |
| `python -m unittest discover -s tests -q` | 102 discovered; 54 passed; 48 skipped; no failures | Skipped infrastructure tests did not pass in this run |
| `python -m ragproof_verifier.evaluate` | 96/96 annotations preserved; 109 supported claims; 0 invalid offsets | Not hallucination precision/recall or independent domain validation |
| `python -m compileall -q src scripts tests` | Completed successfully | Does not validate runtime integrations |
| `node --check src/ragproof_store/static/incident-dashboard.js` | Completed successfully | Does not validate browser behavior/accessibility |
| Import each registered CLI target | 12 resolved; `ragproof-admin` failed because its module is absent | Import success is not end-to-end command success |

The test run emitted a Starlette TestClient/httpx deprecation warning. It is not a test failure, but dependency compatibility should be addressed during packaging work.

Earlier development recorded successful stage-specific and local integration checks, including a Stage 9 full run of 81 tests. The source has changed since then. Historical checks must not be substituted for a fresh all-enabled suite on this snapshot. No new live vendor, browser acceptance, production load, full backup/restore, or authenticated deployment validation is claimed here.

## 12. Security, privacy, and publication boundaries

Keep `.env`, private keys, API tokens, captured private content, runtime traces/replays, backups, database files, virtual environments, and build outputs out of Git. `.env.example` contains placeholders only. The public CUAD fixture is intentionally tracked with attribution.

Current security work is a foundation, not a certification. Token mode has credential scope/role checks, session signing, and origin checks. It does not yet provide complete admin onboarding, independently verified tenant isolation, organization management, SSO, complete retention/deletion workflows, or a demonstrated production encryption/backup policy. Audit events currently need completed-request outcome handling, not merely an initial record.

Secrets belong in local environment configuration or a deployment secret store. Do not paste a token into a new chat, a README example, an API screenshot, or a Git commit. Publishing the repository does not publish the developer's local database or MinIO volumes; a fresh clone starts without those generated records.

## 13. How a new assistant should resume

1. Read this file and `REMAINING_WORK.md` completely.
2. Confirm the actual repository root, Git branch/status, and current user request. Preserve unrelated local changes.
3. Read the relevant source and stage document before modifying that area. Treat older stage documents as historical design notes where this snapshot calls out a mismatch.
4. Start with the concrete blockers and current acceptance tests, not another architecture rewrite or fabricated dataset.
5. Separate implementation, tests actually run, tests skipped, and externally blocked work in every handoff.
6. Ask for vendor access only when necessary; the existing decision is to finish local work without vendor accounts.

Suggested new-chat prompt:

> Continue the RAGProof repository. First read docs/PROJECT_HANDOFF.md and docs/REMAINING_WORK.md, inspect the current code and Git status, and report any differences from that snapshot. We use only real source datasets and original annotations; label controlled fault injection explicitly and do not create fake evaluation evidence. Finish the prioritized local blockers, beginning with the missing admin command and fresh integration/packaging validation. Do not claim Langfuse/LangSmith live testing without actual accounts. Keep secrets and runtime data out of Git. Preserve the existing dark incident UI and improve it with working, accessible workflows rather than decorative fake metrics. Tell me what you need and report verification honestly.

The next implementation plan and acceptance criteria are in [REMAINING_WORK.md](REMAINING_WORK.md).
