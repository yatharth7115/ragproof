# RAGProof — remaining work and acceptance plan

Snapshot date: **2026-10-03**. Companion to [PROJECT_HANDOFF.md](PROJECT_HANDOFF.md). This is a prioritized engineering backlog, not a claim that every missing capability is required before demonstrating the local prototype.

## 1. Where we actually are

The local evidence → verification → diagnosis → replay → regression workflow exists over a pinned real CUAD subset. A dark incident dashboard and strict OTLP receiver exist. The later security, packaging, recovery, backup, and vendor-import pass left partially finished work.

The publication-day check discovered 102 tests: **54 passed, 48 infrastructure-dependent tests skipped**. A separate real-data evaluation preserved all 96 unchanged CUAD annotations (109 claims). Neither result establishes general hallucination detection accuracy or production readiness. The registered `ragproof-admin` entry point currently fails because `ragproof_store.admin` is missing.

Use three release levels:

1. **Reproducible local MVP:** fresh checkout, documented setup, complete local tests, honest UI, no broken advertised commands.
2. **External integration pilot:** genuine external RAG traces, measured failure behavior, confirmed compatibility with at least one real integration.
3. **Production candidate:** security isolation, reliability, operational recovery, independent evaluation, documented capacity and deployment practices.

Finish level 1 before advertising levels 2 or 3. No provider API key is required for most level-1 work.

## 2. Priority 0 — stabilize the current snapshot

### P0.1 Implement or remove the broken administration command

**Evidence:** `pyproject.toml` registers `ragproof-admin = ragproof_store.admin:main`; `.env.example` directs users to it; `src/ragproof_store/admin.py` is absent.

Recommended implementation:

- Add an explicit local CLI for producing a high-entropy API key and its hashed credential record with tenant, project, and role.
- Display the raw key once, with a clear warning; never write it into tracked files or log it through the application.
- Validate role and scope inputs, JSON structure, and configured hash format.
- Decide whether creating project records is a separate explicit operation; do not silently grant access to arbitrary identities.
- Explain storage, rotation, revocation, and session invalidation behavior. Stateless session consequences must be documented.
- If administration is intentionally deferred, remove the advertised entry point and instructions instead of leaving a guaranteed crash.

**Acceptance:** command/import tests pass; malformed roles/scopes fail safely; raw keys are absent from logs and generated public documentation; a generated credential can complete an authenticated local smoke test. Test credentials must be generated locally, not hard-coded production-looking secrets.

### P0.2 Run the full integration suite against a disposable stack

Relevant files: `tests/*integration.py`, `infrastructure/docker-compose.lab.yml`, `src/ragproof_store/`.

- Start PostgreSQL, Redis, and MinIO with health checks in an isolated test environment.
- Check/apply the current migrations deliberately. Do not wipe existing user volumes to make tests pass.
- Run the full suite with `RAGPROOF_RUN_STORAGE_INTEGRATION=1`.
- Diagnose and fix failures caused by the recent security/privacy/outbox changes rather than skipping them.
- Exercise the actual HTTP path through OTLP, storage, verification, diagnosis, replay, regression promotion, and gate reports.
- Run both a passing baseline and a deliberately faulty candidate over real CUAD evidence. Check empty-regression failure too.
- Record command, source commit, dependency environment, counts, skips, and failure details in a reproducible report.

**Acceptance:** one fresh all-enabled run with no unintended skips/failures; a repeated run does not depend on stale state or explode record counts; the healthy candidate passes the declared policy and the faulty candidate fails it. Do not call seven categories seven independent queries.

### P0.3 Prove fresh-install and wheel behavior

Relevant files: `pyproject.toml`, `setup.py`, `MANIFEST.in`, `constraints.txt`, `ragproof_resources`, `Dockerfile`, `Makefile`.

- Build a wheel and install it in a separate clean virtual environment outside the source checkout.
- Check every advertised command with its required extras. Detect dependencies accidentally provided only by the existing development environment.
- Confirm schemas, public fixture, SQL migrations, static UI, and third-party notices are present in the wheel.
- Run laboratory/evaluator/schema loading without source-root path fallbacks.
- Test dependency combinations deliberately: base, development, storage/API/OTLP, integrations, and optional LangSmith.
- Address the TestClient/httpx deprecation and establish a supported dependency matrix rather than relying solely on broad upper bounds.
- Build/run the application image and document actual readiness behavior. Do not assume importing a module proves the container starts.

**Acceptance:** documented fresh-machine commands work; all entry points resolve and applicable smoke commands execute; resources load from the installed wheel; container readiness succeeds with intentional configuration; no secret is included in build context/image layers.

### P0.4 Reconcile documentation and product labels

- Add final setup instructions after the admin/bootstrap path works.
- Keep the distinction between lab mode and authenticated app-stack mode explicit.
- Remove or qualify claims of completed vendor support, generalized replay, automatic PII redaction, or production security until demonstrated.
- Clarify schema examples versus recorded evaluation reports.
- Keep the two handoff documents synchronized with actual test evidence after changes.
- Ask the owner to select a source-code license; do not invent a license choice. Preserve CUAD attribution regardless.

**Acceptance:** a new developer can follow documentation without undocumented credentials or nonexistent commands and can tell which capabilities are lab-only.

## 3. Priority 1 — make local operation trustworthy

### P1.1 Authentication and tenant/project isolation

Current building blocks: `security.py`, authentication middleware, resource ownership lookups, hashed keys, signed cookies, role checks. These need systematic adversarial tests before public exposure.

Work required:

- Test anonymous access, invalid/expired credentials, reader mutation denial, writer/admin behavior, and unknown resources.
- For every trace, response, diagnosis, replay, regression, impact, incident, and gate route, attempt cross-tenant and cross-project reads/writes. Include lists, summaries, counts, and indirect foreign-key lookups.
- Test an entire OTLP batch containing a foreign project: unauthorized input must not leave partially written records.
- Confirm content-object authorization and namespace constraints; knowing an object hash must not grant access.
- Test logout, expiration, key rotation/revocation, cookie flags, same-origin validation, and reverse-proxy HTTPS behavior.
- Review request-size limits and rate limiting. In-memory login counters are not a complete multi-process abuse control.
- Complete request auditing with actual outcome/status, actor, resource, and timestamp; never record raw credentials or private payloads.
- Document the privileged local-mode bypass and make accidental internet exposure difficult.

**Acceptance:** a route-by-route access matrix is automated; no cross-scope record or count leaks; failed writes leave no unauthorized side effects; deployment fails closed on missing token-mode secrets.

### P1.2 Privacy modes and retention

Current code sanitizes selected fields and limits extensions/content references. It does not supply a validated general PII redaction service or end-to-end retention policy.

- Define precisely what `metadata`, `redacted`, and `full` retain at every stage, including spans, nested extensions, artifacts, Redis events, logs, reports, and backups.
- Ensure metadata-only traces cannot leak source/answer text through a secondary field.
- Either implement a tested redaction strategy or clearly name/document the current marker-only behavior.
- Reconcile the extension allowlist with the contract's vendor-neutral preservation goals. Preserve allowed metadata safely without letting arbitrary payloads bypass privacy rules.
- Add explicit retention/deletion workflows for a tenant/project/trace and its derived objects. Handle shared content and references without deleting another scope's evidence.
- Specify backup retention and deletion limitations, encryption/TLS requirements, and operator responsibilities.

**Acceptance:** privacy-mode tests cover all persistence surfaces; deleted data is removed according to a stated retention contract; private source text never appears in metadata-mode exports or logs; absence of evidence yields an honest unverifiable result.

### P1.3 Outbox, retries, and worker recovery

Relevant files: `delivery.py`, `consumer.py`, `events.py`, worker modules, `0002_delivery.sql`.

- Test database commit followed by Redis outage; pending SQL events must eventually be published.
- Crash between publication and SQL acknowledgement; duplicate deliveries must not duplicate irreversible effects.
- Kill workers after receipt and before acknowledgement; pending messages must be recovered.
- Exercise malformed/poison events, retry exhaustion, dead-letter inspection, and deliberate reprocessing.
- Verify concurrent workers do not race to create conflicting verification/replay/regression results.
- Test guards preventing replay-produced and gate-produced traces from causing processing loops.
- Add useful operator metrics: pending age, queue lag, retries, dead letters, last successful dispatch, processing duration.
- Specify retry and retention policies and bounded storage use; an unbounded event stream is not an operational plan.

**Acceptance:** outage/restart tests show eventual processing without lost committed events; duplicates are safe; poison records cannot starve healthy work; the dashboard/API exposes enough state to diagnose stalled processing.

### P1.4 Migration lifecycle

- Test a fresh database, repeated application, concurrent startup, checksum drift, and an unknown/newer migration.
- Test upgrade from a database created before migration bookkeeping was added.
- Ensure startup does not silently reinterpret a partially initialized schema as a successful migration.
- Use new numbered migrations after release instead of changing applied SQL.
- Define a backup-first upgrade procedure and recovery plan for a failed upgrade.

**Acceptance:** upgrades preserve a real CUAD trace's complete lineage and derived records; drift fails explicitly; no procedure requires deleting existing user data.

### P1.5 Backup and restore drill

`backup.py` exists but its presence is not proof of disaster recovery.

- Stop writers/workers intentionally; document downtime and consistency assumptions across SQL, S3, and Redis.
- Back up a populated disposable CUAD stack; verify checksums, manifest completeness, permissions, and migration metadata.
- Restore into a distinct empty stack, never over the user's working database.
- Compare table counts, artifact hashes, trace lineage, verification reports, regression cases, and queue state.
- Test corrupt/truncated manifests, path traversal, incompatible schemas, and refusal to overwrite a nonempty target.
- Measure restore time and document backup security and retention. Confirm Redis serialization/version compatibility assumptions.

**Acceptance:** a recorded successful restore can serve the same evidence and rerun the gate; corrupt backups are rejected; restore has explicit target validation and cannot silently destroy existing work.

## 4. Priority 2 — complete the actual user experience

The current dashboard is a dark incident console, not a complete product workspace. Keep the visual direction, but prioritize working workflows over decorative metrics.

### P2.1 Authentication and onboarding screens

- Add project-key sign-in using the existing session APIs, expiry/unauthorized handling, and logout.
- Explain local mode versus authenticated mode in the UI.
- Show a truthful empty state with instructions for importing an actual trace; do not seed fake activity.
- Show integration configuration status without displaying secret values.
- Give clear feedback when full-content verification is unavailable under metadata capture.

**Acceptance:** a new user can sign in, identify their actual workspace, import an authorized real trace, and inspect it; expired sessions do not appear as mysterious data-plane failures.

### P2.2 Trace and quality-gate workspaces

- Connect the existing trace-list and gate-list endpoints to real screens with paging/filter/search behavior that matches server semantics.
- Build an evidence-first trace detail view: query, component spans, selected/rejected chunks, citations, claim verdicts, and limitations.
- Build gate history/detail views showing candidate identity, policy, coverage, failed cases, and provenance.
- Link incident → original trace → claim/evidence → replay → regression case without losing investigation context.
- Distinguish healthy or undetermined diagnoses from actionable confirmed failures. Use clearly defined denominators for every statistic.

**Acceptance:** every visible count comes from an actual endpoint; every action works or is explicitly unavailable; no placeholder success rate, invented chart history, or dead navigation.

### P2.3 High-quality interaction and accessibility

- Preserve charcoal/light/emerald visual direction; use restrained motion and layered backgrounds.
- Validate reduced-motion settings, keyboard-only navigation, focus restoration/trapping, Escape dismissal, contrast, and screen-reader labels.
- Check compact/mobile and desktop widths, long document titles, long IDs, dense evidence text, and empty/error/loading states.
- Prevent stale detail responses overwriting a newer selection; handle network cancellation and repeated refresh gracefully.
- Test untrusted trace/document text as text, never executable HTML.
- Automate browser smoke tests against real local CUAD-backed API records, not a fake data dashboard.

**Acceptance:** desktop and mobile browser tests pass; no horizontal clipping of essential controls; UI survives API errors and hostile text; animations are optional and do not obscure evidence.

## 5. Priority 3 — validate external integrations

### P3.1 Direct OTLP beyond the controlled sender

- Document the required span/attribute contract and compatibility with complete-tree export.
- Exercise protobuf and JSON, gzip limits, malformed IDs, missing parents, duplicate spans, partial traces, oversized input, and authentication.
- Define whether partial/out-of-order batches are rejected or assembled. Do not guess evidence relationships.
- Measure exporter memory bounds and application latency; synchronous network export must not unexpectedly block customer requests.
- Run an independently instrumented real RAG application with actual retrieved evidence, not merely another invocation of the same lab wrapper.

**Acceptance:** an external application supplies a verifiable trace with preserved lineage; incomplete evidence is rejected or explicitly marked; transport tests cover declared compatibility.

### P3.2 Langfuse and LangSmith live validation

Current import code exists under `ragproof_integrations`; vendor compatibility is **not verified**. User has no projects yet, so this work is externally dependent, not a reason to block local improvements.

When the user is ready, request a chosen service, a genuine trace/run ID, necessary time bounds, and local environment configuration. Never request that keys be pasted into chat.

- Read current official vendor documentation when implementing/testing compatibility.
- Validate actual response shapes, pagination, completed-root selection, child observations, and model/input/output fields.
- Build an explicit mapping from real fields to document/version/chunk/retrieval/answer/citation identifiers.
- Reject missing lineage clearly instead of inventing chunk IDs or treating an arbitrary chat completion as a RAG trace.
- Check rate limits, retry/backoff, bounded pagination, safe endpoint behavior, and sanitized error reporting.
- Import once, reimport idempotently, verify captured evidence, and compare with the source application's actual result.

**Acceptance:** a reproducible, consented live smoke run for each claimed adapter; safe provenance and mapping documentation; no credentials or private trace content committed. Until then label adapters experimental/unverified.

## 6. Priority 4 — establish defensible evaluation and generalized replay

### P4.1 Independent verifier evaluation

The 96 positive annotations test support preservation only. General reliability needs meaningful negatives, paraphrases, numeric/unit changes, negation, conflicting sources, insufficient context, and stale evidence.

- Select licensed, real datasets or consented real application traces with human-reviewed labels appropriate to groundedness.
- Preserve provenance, source versions, original annotations, permissions, and a reproducible sampling manifest.
- Do not manufacture a benchmark with LLM-written examples. Label fault-injection studies separately from real-incident evaluation.
- Separate development, calibration, and held-out evaluation by document/query to avoid leakage.
- Report precision/recall per verdict, abstention rate, confusion matrix, sample sizes, uncertainty, latency, and cost where applicable.
- Compare the lexical baseline with any proposed entailment or model-assisted verifier under identical data and policies.
- If a judge model is used, assess its errors too; a second model is not automatic ground truth.

**Acceptance:** independently interpretable results on unseen real evidence, with limitations and failures published honestly. No “100% accurate” claim based on unchanged excerpts.

### P4.2 Broaden real query coverage and freeze regression evidence

- Generalize the gate beyond the one primary CUAD query.
- Use reviewed, original CUAD annotations across additional documents/questions before adding unrelated domains.
- Store a stable, versioned regression manifest independent of candidate execution.
- Keep candidate code from redefining expected outcomes or silently removing hard cases in the same CI run.
- Report unique queries/documents as well as categories and repeated runs.

**Acceptance:** intentionally breaking one component fails the relevant frozen cases; unrelated query cases still execute; deleting coverage fails policy; a passing gate represents the declared independent corpus.

### P4.3 Customer-pipeline replay interface

- Define an explicit runner contract for pipeline versions, inputs, corpus snapshots, model/configuration identity, and outputs.
- Specify permitted repair dimensions and enforce the one-variable comparison where causal confirmation is claimed.
- Handle nondeterministic LLM runs with repeatability controls/statistical evidence, not one lucky answer.
- Isolate execution; do not run arbitrary customer code inside the API process with storage credentials.
- Preserve original traces and record all replay inputs and limitations.
- Avoid automatic production changes. RAGProof should report evidence; applying a repair requires a separately authorized workflow.

**Acceptance:** an external pipeline can reproduce a baseline and evaluate one controlled change with traceable versions; unsupported runners return a clear limitation rather than silently using CUAD.

### P4.4 Real document version changes

- Capture complete version manifests and content hashes at document/chunk level.
- Distinguish direct use, retrieval-only exposure, citation, and proven claim dependency in impact reports.
- Use actual historical revisions or a licensed versioned corpus for stale-evidence studies.
- Avoid equating every linked answer with an incorrect answer after an update.

**Acceptance:** changing a known real document version yields explainable affected/unaffected answers and explicit uncertainty; missing manifests do not produce invented content-difference conclusions.

## 7. Priority 5 — operations and release readiness

### P5.1 CI and supply-chain controls

- Run unit, all-enabled integration, privacy/security, installed-wheel, and browser checks in appropriate jobs.
- Freeze reviewed regression evidence separately from candidate-generated cases.
- Review/pin Actions and container/dependency versions under a deliberate update policy.
- Add dependency/security checks and a secret scan, including packaged artifacts.
- Preserve useful test/gate reports without exposing captured private content.
- Verify the actual GitHub workflow result; a pushed workflow file is not a green CI run.

**Acceptance:** a clean checkout reproduces the release checks; a known faulty candidate fails CI; artifacts identify the source commit and evidence scope; no necessary check is silently skipped.

### P5.2 Capacity, observability, and deployment

- Test realistic trace sizes and concurrent ingestion, object-store latency, DB pool behavior, queue backlog, and replay costs.
- Add actionable service metrics and structured, privacy-safe logs.
- Define resource limits, readiness/liveness behavior, graceful shutdown, timeout policy, and backpressure.
- Document TLS termination, secret distribution, storage encryption, network restrictions, updates, backups, and recovery.
- Verify authenticated application Compose startup from an empty environment. Current scaffold is not a managed production platform.
- Publish measured supported limits, not invented requests-per-second numbers.

**Acceptance:** a repeatable deployment runbook, tested failure/recovery paths, measured capacity envelope, and an operator can distinguish ingestion failure from analysis delay.

### P5.3 Alerts and webhooks

- Exercise signed failed-gate delivery against an intentionally configured test endpoint.
- Verify signature format, payload minimization, retry/backoff, duplicate-event identity, exhausted retries, and tenant routing.
- Review endpoint validation and network policy to prevent unintended outbound access.
- Provide delivery status and a documented receiver verification example.

**Acceptance:** actual deliveries are observable and correctly scoped; failures retry without duplicate logical incidents; no source content or credentials leak. Do not send alerts to third parties merely to prove the code runs.

## 8. Recommended next implementation sequence

1. Inspect repository state and reproduce publication checks.
2. Repair/administer the broken credential bootstrap path.
3. Run all local infrastructure tests and fix regressions.
4. Prove wheel installation, migrations, and authenticated app-stack startup.
5. Add tenant/privacy/outbox/recovery tests, then a backup/restore drill.
6. Complete sign-in, trace/gate screens, and real-data browser acceptance.
7. Freeze broader real regression coverage and strengthen evaluation.
8. Request genuine vendor access only when ready for the integration pilot.
9. Measure deployment capacity and close operational/security release gates.

At each step, commit a coherent change and report: what changed, what actually ran, the result, and what is still blocked. Do not repeat the earlier broad completion pass without bounded acceptance checks.

## 9. Inputs eventually needed from the owner

- A source-code license decision.
- A real Langfuse or LangSmith project when live integration testing is desired; keys stay in the owner's local environment.
- An independently operated RAG application and permission to inspect its actual traces.
- Deployment target/domain and security/privacy requirements before a public service is launched.
- An approved webhook destination if alerts are to be enabled.
- A model/API budget only if a genuinely model-backed verification or replay experiment is selected.

None of those is needed to repair the missing command or finish the deterministic local test/setup work.

## 10. Definition of completion

The **local MVP** is complete when a new developer can clone, install, start the stack, ingest a real CUAD execution, inspect claim evidence, diagnose and replay a controlled failure, promote a confirmed case, observe a failing/passing gate, and use the dashboard without undocumented fixes; all relevant tests and installed-package checks pass.

An **external pilot** additionally requires real external traces and honest measured integration behavior. A **production release** additionally requires independently tested security/isolation, data lifecycle, recovery, capacity, evaluation, and operational ownership.

The current snapshot has not met all three definitions. Publishing it preserves the work and makes the remaining tasks explicit; publication itself is not project completion.
