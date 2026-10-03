# RAGProof MVP Product Contract

Status: accepted for Stage 0  
Contract version: `0.1.0`

## Product statement

RAGProof is a diagnostic layer on top of existing RAG telemetry. It links
answer claims to evidence, classifies the likely failure stage, replays a failed
request across controlled pipeline variants, and determines the blast radius of
document or configuration changes.

RAGProof is not another general-purpose trace viewer. Langfuse, LangSmith, and
other OpenTelemetry-compatible systems remain valid systems of record;
RAGProof normalizes their traces for reliability analysis.

## Initial customer

The first customer is an engineering team operating a document-heavy internal
assistant whose knowledge changes over time, such as a support, HR, compliance,
or technical-documentation assistant.

The primary user is the engineer responsible for investigating incorrect
answers and preventing regressions. A compliance or product owner is a
secondary reader of incident evidence.

## Job to be done

When a RAG answer is challenged or a knowledge source changes, the user needs
to identify the unsupported claims, reproduce the behavior, isolate the likely
pipeline component, understand affected traffic, and retain the failure as a
regression test.

## Core invariants

1. Every verdict refers to recorded evidence and an immutable pipeline version.
2. Groundedness and objective truth are separate concepts. RAGProof can prove
   support, contradiction, absence, or staleness relative to available sources;
   it cannot guarantee that a source is factually correct.
3. A diagnosis is a ranked, evidence-backed hypothesis until confirmed by a
   controlled replay or a human.
4. Replays change one declared variable at a time unless explicitly labelled as
   multi-variable experiments.
5. Content capture is optional. Metadata-only operation is a supported product
   mode, not an afterthought.
6. Vendor-specific traces are normalized without discarding their original
   identifiers or extension metadata.

## MVP inputs

- OpenTelemetry spans received through OTLP.
- Langfuse traces converted by an adapter.
- LangSmith traces converted by an adapter.
- Pipeline manifests describing component and corpus versions.
- Optional human labels and expected answers.
- Document change events containing old and new content hashes.

## MVP outputs

- Claim-level evidence verdicts with confidence and cited chunks.
- Root-cause hypotheses with observations and recommended experiments.
- Side-by-side replay results for two pipeline versions.
- Affected claims and traces for a changed document version.
- Regression cases created from confirmed incidents.
- CI pass/fail results using explicit reliability thresholds.

All public structured outputs conform to the JSON Schemas in `schemas/`.

## MVP capabilities

### Included

- Canonical, versioned RAG trace representation.
- Direct OTLP ingestion.
- Langfuse and LangSmith import adapters.
- Atomic claim extraction and claim-to-chunk evidence alignment.
- Supported, partially supported, unsupported, contradicted,
  insufficient-context, and stale-evidence verdicts.
- Initial deterministic failure taxonomy.
- Differential replay between a baseline and candidate configuration.
- Reverse lookup from document version to dependent claims and traces.
- Incident inspection and a CLI quality gate.
- Redaction, tenant isolation, retention settings, and metadata-only capture.

### Explicitly excluded

- Replacing Langfuse, LangSmith, or a general APM platform.
- Prompt management, model hosting, or vector-database hosting.
- Fully autonomous remediation in production.
- Universal factuality or hallucination guarantees.
- Training a proprietary evaluation model during the MVP.
- Kubernetes and ClickHouse until measured load requires them.
- Broad agent observability unrelated to retrieval and evidence use.

## Success metrics

The controlled evaluation laboratory will contain labelled failures for every
MVP category. Before an MVP release:

- At least 90% of atomic claims in the labelled set are extracted without
  losing their essential meaning.
- Supported-versus-unsupported classification reaches at least 85% precision
  and 85% recall on that set.
- At least 80% of controlled incidents place the injected component in the top
  two root-cause hypotheses.
- A replay is reproducible from persisted hashes and a pipeline manifest.
- A document change can enumerate every trace that directly consumed the
  changed version in the evaluation laboratory.
- Metadata-only mode stores no prompt, response, or document body.

These are MVP acceptance thresholds, not marketing claims. Results must include
the dataset version and sample size.

## Stage 0 acceptance checklist

- [x] Product boundary and target customer are explicit.
- [x] Canonical trace contract is documented and machine-readable.
- [x] Claim-verification and diagnosis outputs are machine-readable.
- [x] Failure categories have evidence requirements and non-overlapping intent.
- [x] Privacy modes and prohibited data handling are defined.
- [x] Valid examples exist and are checked automatically.
- [x] Stage 1 entry criteria are defined.

## Stage 1 entry criteria

Stage 1 may begin after this contract is reviewed. It must create a small
document assistant over licensed, real-world, human-annotated data, with a
healthy baseline plus one reproducible injected example for each failure
category. Generated questions, answers, or evidence may not serve as evaluation
ground truth. No diagnostic model should be evaluated only on examples it
generated itself.
