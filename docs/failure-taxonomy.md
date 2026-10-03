# MVP Failure Taxonomy

The taxonomy identifies the earliest observed pipeline stage capable of
explaining a failed claim. A category is assigned only when its minimum evidence
is available. Otherwise the diagnosis remains `UNDETERMINED` internally and
requests a specific experiment.

## Categories

### `PARSING_FAILURE`

The authoritative source contains the needed information, but the parsed
representation omits or corrupts it.

Minimum evidence: comparison between the source artifact and parsed output.

Typical confirmation: rerun with a parser that preserves the missing structure.

### `CHUNKING_FAILURE`

The parsed representation is correct, but segmentation destroys the context
needed to retrieve or interpret the evidence.

Minimum evidence: source spans and chunk boundaries showing information split,
truncated, duplicated, or combined incorrectly.

Typical confirmation: replay with a different chunker while holding all other
components constant.

### `RETRIEVAL_FAILURE`

A suitable indexed chunk exists, but the retriever does not return it within
the configured candidate set.

Minimum evidence: corpus/index search demonstrating that the evidence exists,
plus the recorded retrieval candidate list.

Typical confirmation: replay with a retrieval strategy or `top_k` change.

### `RERANKING_FAILURE`

The retriever returns suitable evidence, but the reranker removes it or places
it outside the context-selection threshold.

Minimum evidence: pre-rerank and post-rerank candidate positions.

Typical confirmation: bypass or replace the reranker.

### `GENERATION_FAILURE`

Sufficient evidence is included in the model context, but the answer introduces
an unsupported claim or contradicts that evidence.

Minimum evidence: exact final context, generated claim, and entailment or
contradiction result.

Typical confirmation: regenerate with identical context and a controlled prompt
or model change.

### `CITATION_FAILURE`

The answer may be correct or grounded elsewhere, but the attached citation does
not support the cited claim or refers to a nonexistent source.

Minimum evidence: claim-to-citation alignment and the cited source content.

Typical confirmation: rebuild citations from verified claim-evidence links.

### `STALE_KNOWLEDGE`

The answer is supported by a captured source version that has been superseded
under the project's freshness policy.

Minimum evidence: consumed document version, current document version, and an
effective or superseded timestamp.

Typical confirmation: replay against the current corpus version.

## Orthogonal observations

`CONFLICTING_SOURCES`, `INSUFFICIENT_CONTEXT`, and `EVALUATOR_DISAGREEMENT` are
observations rather than root causes in the first MVP. They can accompany any
category and may prevent automatic confirmation.

## Diagnosis lifecycle

```text
suspected → replaying → confirmed
                  └──→ rejected
         └───────────→ needs_review
```

A heuristic or model prediction can produce `suspected`. Only a controlled
replay or human review can produce `confirmed`.

## Precedence

When multiple stages fail, report the earliest causal stage as primary and
retain downstream consequences as secondary observations. For example, damaged
parsing may cause retrieval failure; the primary category is parsing when the
source-to-parse comparison proves the damage.
