# Retrieval Quality Benchmark v0.2

**Status:** corrected post-merge design for Issue #6

## Claim boundary

This benchmark measures retrieval quality only:

> Given a known repeated-error scenario, does a retrieval strategy rank the correct validated lesson above decoys?

It does not measure whether a real LLM changes its decision, reduces production incidents, or behaves consistently across repeated runs.

## Ground truth

Each scenario contains an `ErrorSignature`:

```text
(domain, task_type, pattern_id, version)
```

Two errors are the same only when all four normalized fields match exactly. All fields, including `version`, are required explicitly.

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
- `false_discovery_rate`: returned non-relevant lessons divided by all returned lessons; empty output is `0`;
- `decoy_selection_rate`: returned known decoys divided by all known decoys; no known decoys produces `0`;
- `returned_score_gap`: best returned relevant score minus best returned decoy score.

`returned_score_gap` is defined only when both a relevant lesson and at least one known decoy are returned. It is `null` when either side is absent. Filtered or access-denied memories are never scored merely to manufacture a benchmark gap.

Aggregate metrics are arithmetic means across scenarios. Mean returned score gap uses only scenarios with a defined gap and reports `null` when none are defined.

## Metric interpretation

False discovery rate and decoy selection rate answer different questions:

```text
FDR = returned non-relevant lessons / all returned lessons
DSR = returned known decoys / all known decoys
```

FDR measures output cleanliness. DSR measures how much of the known negative fixture set leaked into the output.

## Fixtures

The suite contains three QA scenarios:

1. desktop-only checkout validation misses Android Chrome;
2. a cross-system transfer timeout has inconsistent statuses;
3. release regression omits checks derived from a prior production incident.

Each fixture contains one relevant lesson and four plausible decoys. IDs and timestamps are fixed so reports are reproducible.

## Output

The runner writes:

- `retrieval-quality.json` for machines;
- `retrieval-quality.md` for humans.

The report version is `retrieval-quality-v0.2`. The same source tree and fixed benchmark time must produce byte-equivalent reports.

## Breaking changes from v0.1

- `false_positive_rate` → `false_discovery_rate`;
- new `decoy_selection_rate`;
- `score_gap` → `returned_score_gap`;
- corresponding aggregate fields renamed;
- `ErrorSignature.version` became required.

## Deferred levels

Level 2 may add a deterministic decision-policy simulation. The policy must receive raw runtime-visible retrieval output, never ground-truth-derived benchmark fields. Real LLM behavioral evaluation requires a separate design covering model versioning, repeated trials, variance, cost, and confidence intervals.
