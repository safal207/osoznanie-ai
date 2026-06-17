# Retrieval Quality Benchmark v0.1

**Status:** implementation design for Issue #6

## Claim boundary

This benchmark measures retrieval quality only:

> Given a known repeated-error scenario, does a retrieval strategy rank the correct validated lesson above decoys?

It does not measure whether a real LLM changes its decision, reduces production incidents, or behaves consistently across repeated runs.

## Ground truth

Each scenario contains an `ErrorSignature`:

```text
(domain, task_type, pattern_id, version)
```

Two errors are the same in v0 only when all four normalized fields match exactly.

`ErrorSignature` is benchmark-only. It is not yet part of the Osoznanie protocol or core memory model.

## Strategies

### NoMemory

Returns no lessons. This is the zero-information baseline.

### NaiveKeywordSearch

Ranks lesson statements by deterministic query-token overlap. It intentionally does not use structured scope, validation status, provenance, trust, recency, or access policy.

### OsoznanieRecall

Adapts `RecallEngine` results to the benchmark ranking interface.

Strategies receive only the query, store, and fixed evaluation time. They never receive relevant lesson IDs or the error signature.

## Metrics

For each scenario and strategy:

- `hit_at_1`: a relevant lesson is ranked first;
- `hit_at_3`: a relevant lesson appears in the first three results;
- `reciprocal_rank`: `1 / rank` of the first relevant lesson, or `0`;
- `false_positive_rate`: returned decoys divided by all returned results; empty output is `0`;
- `score_gap`: best relevant score minus best decoy score, or `null` when no relevant result is returned.

Aggregate metrics are arithmetic means across scenarios. Mean score gap uses only scenarios with a defined gap and reports `null` when none are defined.

## Fixtures

The first suite contains three QA scenarios:

1. desktop-only checkout validation misses Android Chrome;
2. a cross-system transfer timeout has inconsistent statuses;
3. release regression omits checks derived from a prior production incident.

Each fixture contains one relevant lesson and four plausible decoys. IDs and timestamps are fixed so reports are reproducible.

## Output

The runner writes:

- `retrieval-quality.json` for machines;
- `retrieval-quality.md` for humans.

The same source tree and fixed benchmark time must produce byte-equivalent reports.

## Deferred levels

Level 2 may add a deterministic decision-policy simulation. Real LLM behavioral evaluation requires a separate design covering model versioning, repeated trials, variance, cost, and confidence intervals.
