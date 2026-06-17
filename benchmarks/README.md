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

The scenario also lists explicit relevant and decoy lesson IDs. Strategies never receive those labels.

## Metrics

- hit rate at 1;
- hit rate at 3;
- mean reciprocal rank;
- false-positive rate;
- score gap between the best relevant result and best decoy.

## Interpretation

A better result means a strategy ranked the synthetic fixture's known lesson more effectively. It does not prove that a language model would follow the lesson, change its decision, or prevent a production incident.
