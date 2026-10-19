# Local credential setup

The deterministic laboratory uses `RAGPROOF_AUTH_MODE=local`. The separate
application stack in `infrastructure/docker-compose.app.yml` uses `token` mode
and requires project credentials and a session signing secret before startup.

## Generate a project credential

After installing the package, run this command in a private local terminal:

```console
ragproof-admin create-key --tenant-id cuad-public --project-id cuad-contract-review --role writer
```

The command prints the **raw API key once on stderr** and a JSON array containing
its **SHA-256 hash on stdout**. Save the raw key in your password manager. Avoid
running key generation in CI or another environment that collects terminal output
as logs. The command does not create files, modify configuration, connect to the
data plane, or log through the application. Credential generation works with the
base package; running the server additionally requires the `api`, `storage`, and
`otlp` extras.

Set `RAGPROOF_API_KEYS_JSON` in your untracked local environment file to the JSON
array printed on stdout. For additional credentials, combine the records into one
array. Every record must contain exactly `key_hash`, `tenant_id`, `project_id`,
and `role`. Hashes must be unique, lowercase, 64-character SHA-256 hex digests.
Scope IDs must be 1–256 printable characters with no surrounding whitespace.
Malformed configuration prevents startup. Never put the raw key in this JSON.

Choose the role explicitly:

| Role | API permissions |
| --- | --- |
| `reader` | Read resources in the credential's tenant and project |
| `writer` | Read and mutate resources in that tenant and project |
| `admin` | Currently the same API access as writer; no cross-project bypass |

Scope values identify the project you intend to authorize. They are not checked
against a database by this offline command. Installing a hash record in server
configuration explicitly grants access to that scope. The existing ingestion path
creates tenant/project rows on the first authorized trace; this command creates
neither those records nor any trace. There is no separate project provisioning
CLI yet. Use the CUAD scope shown above for the existing laboratory captures.

## Configure and start the authenticated stack

Copy `.env.example` to an untracked `.env` file and replace every `REPLACE` value.
Generate the session signing secret locally with:

```console
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set it as `RAGPROOF_SESSION_SECRET`. Generate independent random values for the
database and object-store credentials. Keep the database password consistent
between `RAGPROOF_DB_PASSWORD` and `RAGPROOF_POSTGRES_DSN`, and URL-encode it in
the DSN if necessary. The app stack uses database `ragproof` and internal host
`postgres`; the lab defaults use a separate database and loopback ports.

Use `RAGPROOF_COOKIE_SECURE=false` only for deliberate loopback HTTP testing;
HTTPS deployments require `true`. From the repository root:

```console
docker compose --env-file .env -f infrastructure/docker-compose.app.yml up -d --build --wait
```

This is the intended startup command, not evidence of a completed deployment
test. The currently pinned MinIO image failed to pull in the recorded hosted CI
run; see `REMAINING_WORK.md` P0.2 before attempting full-stack validation.

`GET /auth/status` reports whether authentication is required. API clients send
the raw key as `Authorization: Bearer <key>`. Browser clients can POST
`{"api_key":"<key>"}` to `/auth/session` on the same origin to receive an HttpOnly,
SameSite=Strict session cookie, then DELETE `/auth/session` to clear it. Send keys
only over HTTPS or deliberate loopback HTTP. The authenticated API tests use
generated keys and real CUAD captures with an in-memory recording repository;
they do not prove persistence or full-stack tenant isolation.

## Rotate, revoke, and sign out

Credentials are loaded when the API process starts. To rotate a key, generate a
new key for the desired scope and role, temporarily include both hash records,
and recreate every API process/container with the updated configuration. Migrate
clients to the new key, then remove the old record and recreate every API process
again. Environment changes alone do not update an existing process.

Removing a hash record revokes both that bearer key and cookies signed for it
after all API processes reload. Browser sessions expire after eight hours and
are resolved against the current records on each request. Changing
`RAGPROOF_SESSION_SECRET` and recreating every API process invalidates all browser
sessions while preserving configured bearer keys.

Logout clears the browser's cookie. Sessions are stateless: a copied cookie
remains usable until expiration, removal of its credential record, or replacement
of the signing secret. There is no per-session server-side revocation list.
