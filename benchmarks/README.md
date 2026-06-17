# Osoznanie Benchmarks

The repository contains two deterministic benchmark levels for known repeated-error scenarios, plus a split public/restricted decision-path layer.

> [!WARNING]
> Level 1 and Level 2 use authored synthetic fixtures whose expected outcomes are known by design. The reports validate deterministic pipeline behavior. They do not measure real LLM behavioral impact, live-agent improvement, or real-world incident reduction.

Every JSON report carries the same limitation as a structured `SyntheticClaim` object with `scope`, `fixture_count`, `policy_kind`, and `disclaimer` fields.

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

Strategies receive only query, storage, and fixed evaluation time. They never receive relevant IDs or error signatures.

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
        ├── <scenario>--<strategy>.public.json
        ├── <scenario>--<strategy>.public.mmd
        └── <scenario>--<strategy>.audit.json
```

### Leakage boundary

The policy sees only:

- runtime `DecisionTask`;
- ordered lesson ID, statement, and rank;
- benchmark-only structured action recommendation.

The policy never sees:

- retrieval strategy name;
- retrieval score or score breakdown;
- provenance;
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

## Decision-path artifacts

Each completed trial is retained once as an exact typed retrieval snapshot. Artifacts are generated from that completed trial; retrieval is not replayed during trace generation.

```text
task -> retrieval -> returned lessons -> policy -> decision -> evaluated outcome
```

### Public files

`.public.json` and `.public.mmd` contain stable node/edge IDs, lesson IDs and ranks, recommendations, the selected action, status, and reason code. They exclude:

- retrieval scores and score breakdowns;
- typed provenance;
- lesson statements;
- private chain-of-thought;
- hidden error signatures;
- access-denied memory contents or identifiers.

### Restricted file

`.audit.json` adds forensic data only for lessons actually returned to the policy:

- `canonical_score`;
- typed `ScoreBreakdown`;
- typed `ReasonCode` values;
- `provenance_refs: list[ProvenanceRef]`;
- one validated `RankingPolicyRef` at artifact level.

`Decimal` fields are serialized as JSON strings in ordinary decimal notation. Consumers must parse them with a decimal-aware parser rather than binary floating-point arithmetic.

Access-denied candidates are absent from both public and restricted lesson lists. Privacy-aware aggregate filter counters are defined in a separate follow-up contract.

### Closed path classification

Possible statuses are:

- `safe_decision`;
- `repeated_error`;
- `abstention`;
- `alternate_action`.

Every status requires its matching structured reason code. There is no `other` bucket.

### Manifest boundary

`decision-path-manifest.json` is a generated index, not a second source of trial content. It contains artifact paths, strategy, status, and reason code. Full trial data remains in the public and restricted per-trial files.

## Key Finding: Application Rate ≠ Decision Quality

**Lesson application alone is not evidence of improved decision quality.**

In the deterministic benchmark, naive keyword retrieval achieved:

- **100% lesson application rate**;
- **0% correct decisions**;
- **100% repeated-error rate**.

The policy consistently applied highly ranked decoy lessons. This demonstrates a failure mode in naive RAG systems: irrelevant context can be applied confidently and create a false sense of correctness.

Osoznanie Recall achieved correct decisions on these fixtures not by increasing application rate, but by ensuring the applied lesson passed structured scope, access, validation, and temporal eligibility checks.

## Interpretation boundary

Level 1 measures retrieval against fixture-defined relevance. Level 2 measures one deterministic reference policy against fixture-defined actions. Decision-path artifacts expose the auditable route through that synthetic pipeline. None of these outputs establishes production-agent improvement.
