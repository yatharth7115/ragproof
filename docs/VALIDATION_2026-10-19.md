# RAGProof continuation — 2026-10-19

Starting source: `a79ace3d492f45e58efcbf1f6e6c4dcf58ac55e1` on `main`.
Development branch: `fix/credential-bootstrap`.
Initial local environment: Windows, Python 3.12.14, dependencies installed with
`constraints.txt` and the `dev,api,otlp,storage,integrations` extras.

## Current hosted result

[Actions run 39301460542](https://github.com/yatharth7115/ragproof/actions/runs/39301460542)
passed all three jobs for PR head `57171da6e7c4dc0999199ea175d51ad0c1e965ae`:

| Check | Observed result |
| --- | --- |
| Unit and installed-wheel jobs | Passed on Python 3.10 and 3.12 |
| PostgreSQL, Redis, and pinned-source MinIO startup | All healthy |
| First enabled integration suite | 116 discovered: **114 passed, two live-vendor tests skipped**, zero failures/errors; 28.198 seconds |
| Repeat against the same stack | **114 passed, two live-vendor tests skipped**, zero failures/errors; 28.357 seconds |
| Generated-key persistence and browser sessions | Passed against actual PostgreSQL/S3/Redis, including scoped lineage reads and reader/foreign-project denial |
| Controlled faulty-candidate gate | Expected FAIL verified by integration tests and CLI checks |
| Empty regression set | Fail-closed behavior verified by integration tests |
| Final healthy regression gate | PASS: seven cases, all seven required categories, zero failed cases |

The final healthy candidate identity was
`github-bbe1b747fcd023b1d1d8d849bdb2cc0f0c372265` (the PR merge checkout).
Seven failure categories around the primary CUAD query are not seven independent
questions or a broad-domain accuracy benchmark. The only skipped tests require
real Langfuse/LangSmith accounts. The runner fails if any other test is skipped.

Both runs' source/dependency/count summaries are retained in the
[integration-summaries artifact](https://github.com/yatharth7115/ragproof/actions/runs/39301460542/artifacts/12211088866).
Per-trace and replay/case idempotence checks passed again against existing state;
this does not establish production retention, queue capacity, or load limits.

## What already exists

| Area | Code present | Current evidence / remaining limitation |
| --- | --- | --- |
| Contracts and CUAD laboratory | Canonical schemas; original annotated fixture; deterministic healthy and fault scenarios | Contract/laboratory checks pass. Fault injection is controlled, not real customer incidents. |
| OpenTelemetry | Span capture, canonical mapping, strict OTLP receiver, external canonical HTTP client | Local capture/protocol/client checks pass. An independently operated RAG app has not been validated. |
| Evidence storage and processing | PostgreSQL/pgvector, S3-compatible objects, Redis streams, workers | All 48 data-plane tests now pass in hosted CI, including the two new authenticated persistence checks. |
| Verification and diagnosis | Claim extraction, lexical/numeric verification, ranked root-cause hypotheses | Local checks pass; 96 positive annotations preserved. General hallucination precision/recall is unmeasured. |
| Replay, impact, regressions, gate | One-variable CUAD replays, document lineage, case promotion, CLI/API gate | Fresh and repeated hosted integration checks passed; final gate passed seven categories. Coverage centers on one primary query. |
| Incident UI | Existing dark incident dashboard and detail assets | No new browser acceptance run. Sign-in, trace/gate workspaces, and accessibility work remain. |
| Operations and integrations | Auth middleware, migrations, outbox, backups, vendor import adapters, Compose scaffolds | These require isolation/recovery/deployment checks. No live Langfuse/LangSmith validation. |

## Implemented in this continuation

- Added `ragproof-admin create-key` with required tenant/project/role, 256-bit
  random keys, one-time secret display on stderr, and hashed configuration on
  stdout. It performs no database writes or automatic project provisioning.
- Shared credential validation now rejects invalid JSON structure, malformed
  SHA-256 hashes, missing/extra fields, invalid roles/scopes, and duplicate hashes.
- Made `ragproof_store` exports lazy so local credential bootstrap works before
  optional API and storage dependencies are installed.
- Added 10 tests for generated credentials and authenticated API behavior,
  including roles, foreign scopes, browser sessions, expiration, rotation, and
  revocation. HTTP tests use a recording repository, not a persisted data plane.
- Documented local credential setup, project-creation behavior, process reloads,
  and stateless logout/session limitations. Updated README, environment guidance,
  handoff, and backlog.
- Added `scripts/check_installed_package.py` and an independent CI job for Python
  3.10/3.12, covering base/full wheel installation outside the checkout, packaged
  resources, advertised CLI help, and tests against the installed wheel.
- Replaced the retired MinIO image in both Compose stacks with a local build of
  upstream commit `9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a`, including its license.
  Startup is verified in CI. Community MinIO is archived; this restores the lab
  dependency rather than selecting a maintained production object store.
- Repaired the missing closing `$$` in the never-valid delivery migration. Its
  original syntax could not commit through the transactional migrator. Checksums
  for valid applied migrations are still enforced; no volumes were wiped.
- Added two real data-plane authentication tests and a CI runner that rejects
  unintended skips and saves summaries. Two runner tests verify accurate success
  counts when fixtures or multiple subtests fail.

## Earlier local checks

| Check | Result |
| --- | --- |
| `python -m unittest discover -s tests -v` | 112 discovered: **64 passed, 48 skipped, zero failures/errors** |
| Skip breakdown | 46 require PostgreSQL/Redis/S3 data plane; two require real vendor accounts |
| Credential/API tests | All 10 passed with runtime-generated keys; no retained secret output |
| `ragproof-evaluate` | PASS: 96/96 original annotations preserved, 109 supported claims, zero invalid source offsets |
| `python -m pip check` | No broken requirements |
| `python -m compileall -q src/ragproof_store scripts/check_installed_package.py tests/test_admin_credentials.py` | Passed |
| `git diff --check` | Passed |
| Wheel build | Built `ragproof_mvp-0.1.0-py3-none-any.whl` with local extras and constrained dependencies |
| Fresh base install outside checkout | Passed: admin/lab/capture commands, actual healthy CUAD run, packaged resources, and generated credential |
| Fresh install with all local extras outside checkout | Passed: all 13 installed CLI help commands, packaged resources, healthy CUAD lab/capture/evaluation, and generated credential |
| Suite against the installed wheel, from outside checkout | 112 discovered: **64 passed, 48 skipped, zero failures/errors**; modules resolve inside the clean environment's `site-packages` |

Both clean environments also passed `pip check`. The wheel SHA-256 was
`ee578b8f967b7c84168bb7076902531efeaea59fb50891aedcea1a2d1283a517`.

The test run still emits the existing Starlette TestClient/httpx deprecation
warning. The eight-hour session expiry, removal of a key record, and signing
secret changes are tested directly; browser logout only clears the current
cookie and does not revoke a copied cookie server-side.

The local suite required execution outside the Windows sandbox because the sandbox
blocked the local sockets used by asyncio/TestClient. No product workaround was
added for that environment restriction. Docker is unavailable on this machine;
the data-plane tests were not enabled locally. The later hosted evidence above
supersedes those local skips for data-plane validation.

### Reproduce installation checks

From the repository root, with a suitable Python environment:

```console
python -m pip wheel -c constraints.txt '.[dev,api,otlp,storage,integrations]' --wheel-dir ../wheelhouse
```

Create separate clean virtual environments outside the source checkout. Install
the base package from the wheelhouse in one, and
`ragproof-mvp[dev,api,otlp,storage,integrations]` in the other, using
`--no-index --find-links <wheelhouse>`. From outside the checkout, run:

```console
<base-python> <repository>/scripts/check_installed_package.py --base
<full-python> <repository>/scripts/check_installed_package.py
```

Use the full environment to run
`python -m unittest discover -s <repository>/tests -v` from outside the checkout.
On Windows, environment executables are under `Scripts/`;
on Linux/macOS, they are under `bin/`. Installed-wheel help checks do not prove
service-dependent commands can complete without a running data plane.

## CI startup failure diagnosed

Read [Actions run 37126668648](https://github.com/yatharth7115/ragproof/actions/runs/37126668648),
job `111213244408`. The recorded startup failure is:

```text
pull access denied for minio/minio, repository does not exist or may require 'docker login'
```

The PostgreSQL and Redis image pulls were interrupted after the MinIO pull
failed. The integration suite, promotion, and gate did not run in that historical
run. The pinned source build restored startup in run 39300755745 and exposed the
invalid delivery migration. After the migration repair, run 39301460542 passed
startup, both integration suites, promotion, and the final gate. The unit/wheel
job remains independent of data-plane startup.

## What is left, in order

1. Build/run the RAGProof application image, validate its readiness behavior, and
   start the authenticated application Compose stack with deliberately configured
   secrets. The existing hosted evidence runs the API/tests against the lab data
   plane; it does not prove the separate application deployment.
2. Finish optional LangSmith
   installation checks, and the dependency matrix/deprecation work. Verify new
   hosted CI results; adding a workflow is not a green run.
3. Extend tenant/project isolation and privacy tests across every resource; finish
   request audit outcomes, outbox/retry recovery, migrations, and a backup/restore
   drill.
4. Finish UI sign-in, truthful onboarding, trace/gate navigation, and browser and
   accessibility acceptance over real CUAD-backed records.
5. Broaden independent real-data evaluation and regression coverage; validate
   external pipeline replay, document revisions, and real vendor imports when
   actual accounts/traces are available.

A source-code license still needs an owner decision. No vendor secrets are
needed for the next local stabilization work.
