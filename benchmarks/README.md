# Retrieval Quality Benchmark

This benchmark compares three deterministic retrieval conditions for known repeated-error scenarios:

- `no_memory`
- `naive_keyword`
- `osoznanie_recall`

It measures retrieval quality, not real LLM behavior.

## Run

```bash
python -m benchmarks.run --output benchmark-results
```

The command writes:

```text
benchmark-results/
├── retrieval-quality.json
└── retrieval-quality.md
```

## Ground truth

Each scenario defines an exact benchmark-only `ErrorSignature`:

```text
(domain, task_type, pattern_id, version)
```

All four fields are required. The scenario also lists explicit relevant and decoy lesson IDs. Strategies never receive those labels.

## Metrics

- `hit_at_1`: a relevant lesson is ranked first;
- `hit_at_3`: a relevant lesson appears in the first three results;
- `reciprocal_rank`: `1 / rank` of the first relevant lesson, or `0`;
- `false_discovery_rate`: returned non-relevant lessons divided by all returned lessons; empty output is `0`;
- `decoy_selection_rate`: returned known decoys divided by all known decoys; no known decoys produces `0`;
- `returned_score_gap`: best returned relevant score minus best returned decoy score.

`returned_score_gap` is `null` unless the strategy returns both a relevant lesson and at least one known decoy. A strategy that returns only the correct lesson therefore has no measurable returned score gap rather than an artificial gap against zero.

## Breaking report changes in v0.2

- `false_positive_rate` was renamed to `false_discovery_rate`;
- `decoy_selection_rate` was added;
- `score_gap` was renamed to `returned_score_gap`;
- aggregate fields use the same corrected names;
- `ErrorSignature.version` is now required explicitly.

Any downstream consumer of `retrieval-quality.json` must update to the v0.2 field names.

## Interpretation

A better result means a strategy ranked the synthetic fixture's known lesson more effectively. It does not prove that a language model would follow the lesson, change its decision, or prevent a production incident.
