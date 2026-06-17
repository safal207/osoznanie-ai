# Osoznanie Benchmarks

The repository contains two deterministic benchmark levels for known repeated-error scenarios.

## Level 1 — Retrieval quality

Compares:

- `no_memory`
- `naive_keyword`
- `osoznanie_recall`

Run:

```bash
python -m benchmarks.run --output benchmark-results/retrieval
```

Outputs:

```text
benchmark-results/retrieval/
├── retrieval-quality.json
└── retrieval-quality.md
```

### Retrieval ground truth

Each scenario defines an exact benchmark-only `ErrorSignature`:

```text
(domain, task_type, pattern_id, version)
```

All four fields are required. Strategies receive only query, storage, and fixed evaluation time; they never receive relevant IDs or error signatures.

### Retrieval metrics

- `hit_at_1`;
- `hit_at_3`;
- `reciprocal_rank`;
- `false_discovery_rate`;
- `decoy_selection_rate`;
- `returned_score_gap`.

`returned_score_gap` is `null` unless both a relevant lesson and at least one known decoy are returned.

## Level 2 — Deterministic decision simulation

Runs the same transparent `TopActionableLessonPolicy` over the raw outputs of all three retrieval strategies.

Run:

```bash
python -m benchmarks.run_simulation --output benchmark-results/decision
```

Outputs:

```text
benchmark-results/decision/
├── decision-simulation.json
└── decision-simulation.md
```

### Leakage boundary

The policy sees only:

- runtime `DecisionTask`;
- ordered lesson ID, statement, and rank;
- benchmark-only structured action recommendation.

The policy never sees:

- retrieval strategy name;
- retrieval score;
- `ErrorSignature`;
- relevant lesson IDs;
- safe or repeated-error labels;
- retrieval evaluation metrics.

### Decision metrics

- `correct_decision_rate`;
- `repeated_error_rate`;
- `lesson_application_rate`;
- `abstention_rate`;
- `policy_coverage`.

The evaluator calls the policy twice for each identical input. Different outputs fail the benchmark with `NonDeterministicPolicyError`.

## Interpretation boundary

Level 1 shows whether a strategy retrieves the fixture's known lesson. Level 2 shows how those retrieval outputs change one deterministic reference policy.

Neither benchmark proves that a real language model would follow a lesson, change its decision, or prevent a production incident.
