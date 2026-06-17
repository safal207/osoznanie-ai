# Decision Path Graphs v0.1

Decision Path Graphs provide an auditable representation of each deterministic benchmark trial:

```text
task -> retrieval -> returned lessons -> policy -> decision -> evaluated outcome
```

The graph is generated from the completed `DecisionTrialResult`, not by re-running retrieval. This guarantees that the trace represents the exact trial being evaluated.

## Artifacts

Each trial writes:

- one JSON graph;
- one Mermaid flowchart;
- one manifest entry.

Node IDs are stable and Mermaid-safe. Graphs contain lesson IDs, ranks, action recommendations, policy decisions, and final evaluator status.

## Privacy boundary

Graphs exclude:

- lesson statements;
- retrieval scores;
- hidden error signatures;
- access-denied memory contents;
- private chain-of-thought.

Only the final outcome node is marked evaluator-only.

## Claim boundary

The graph visualizes a deterministic benchmark path. It does not reconstruct private reasoning or prove how a real LLM arrived at a decision.
