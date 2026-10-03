# Privacy and Security Contract

RAG telemetry can contain credentials, personal data, confidential documents,
and regulated information. Content collection is therefore opt-in and governed
per project.

## Capture modes

### `full`

Prompt, response, and evidence content may be captured or stored through an
encrypted reference. This mode requires explicit project configuration.

### `redacted`

Content is passed through configured secret and PII redaction before durable
storage. Redaction failures quarantine the trace rather than falling back to
full capture.

### `metadata_only`

Only identifiers, hashes, ranks, scores, versions, timings, token counts, and
evaluation labels are retained. Prompt, response, and document bodies are
prohibited.

## MVP controls

- Tenant and project IDs are mandatory on every trace.
- Authorization checks use both tenant and project boundaries.
- Secrets are loaded from environment variables or a secret manager and never
  written to traces.
- Raw content and replay artifacts are encrypted at rest and in transit.
- Retention is configurable separately for metadata and content.
- Deletion can target a tenant, project, trace, user, or document version.
- Export and deletion actions create audit records.
- Evaluation providers receive only the minimum required fields.
- Sampling decisions occur after mandatory security filtering.

## Prohibited behavior

- Logging API keys, authentication headers, cookies, or connection strings.
- Recording content in metadata-only mode, including inside vendor extensions.
- Using customer content to train shared models without a separate explicit
  agreement.
- Sending captured content to an external evaluator when the project is
  configured for local-only processing.
- Treating hashing as anonymization when the original text is guessable.

## Stage 0 threat assumptions

The MVP assumes an authenticated engineering user and does not yet satisfy a
specific regulatory certification. Stage 1 fixtures must use appropriately
licensed public data with recorded provenance. Real private customer content is
prohibited until access control, deletion, audit, and redaction tests are
implemented.
