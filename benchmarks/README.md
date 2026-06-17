# Osoznanie Benchmarks

The repository contains two deterministic benchmark levels for known repeated-error scenarios, plus an auditable decision-path trace layer.

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
├── decision-simulation.md
└── decision-paths/
    ├── decision-path-manifest.json
    └── graphs/
        ├── <scenario>--<strategy>.json
        └── <scenario>--<strategy>.mmd
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

## Decision Path Graphs

Each decision-simulation trial emits a deterministic graph:

```text
task -> retrieval -> returned lessons -> policy -> decision -> evaluated outcome
```

The JSON graph provides stable node and edge IDs for machine analysis. The Mermaid file provides a human-readable flowchart without requiring an external rendering dependency during CI.

Graph nodes contain IDs, ranks, action recommendations, policy decisions, and evaluator status. They deliberately exclude:

- retrieval scores;
- lesson statements;
- private chain-of-thought;
- hidden error signatures;
- access-denied memory contents.

Only the final outcome node is marked `evaluator_only`. This keeps runtime-visible path data separate from hidden correctness labels.

Possible graph statuses are:

- `safe_decision`;
- `repeated_error`;
- `abstention`;
- `other`.

The graph layer is an audit trail for the deterministic benchmark path. It is not a reconstruction of private reasoning and does not claim to expose how a real LLM thinks.

## Key Finding: Application Rate ≠ Decision Quality

**Lesson application alone is not evidence of improved decision quality.**

In the deterministic benchmark, naive keyword retrieval achieved:

- **100% lesson application rate**;
- **0% correct decisions**;
- **100% repeated-error rate**.

The policy consistently applied highly ranked decoy lessons. This demonstrates a critical failure mode in naive RAG systems: irrelevant context can be applied confidently and create a false sense of correctness.

Osoznanie Recall achieved correct decisions not by increasing application rate, but by ensuring the applied lesson passed structured scope, access, validation, and temporal eligibility checks.

## Interpretation boundary

Level 1 shows whether a strategy retrieves the fixture's known lesson. Level 2 shows how those retrieval outputs change one deterministic reference policy. Decision-path graphs show the auditable route through that synthetic pipeline.

None of these benchmarks proves that a real language model would follow a lesson, change its decision, reveal its private reasoning, or prevent a production incident.
