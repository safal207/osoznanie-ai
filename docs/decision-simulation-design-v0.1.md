# Deterministic Decision-Policy Simulation v0.1

**Status:** implementation design for Issue #12

## Claim boundary

This benchmark measures a deterministic policy simulation:

> Under the same transparent decision policy, do different retrieval strategies change whether a known repeated-error action is selected?

It does not measure real LLM behavior, persuasion, compliance, or production incident reduction.

## Evaluation-leakage boundary

The policy receives only runtime-visible data:

- `DecisionTask`;
- ordered `PolicyLesson` records;
- structured benchmark-only action recommendations attached to returned lessons.

The policy never receives:

- `ErrorSignature`;
- relevant lesson IDs;
- safe or repeated-error labels;
- retrieval strategy name;
- retrieval scores;
- hit-rate, reciprocal-rank, score-gap, or other evaluator metrics.

Only the evaluator can access hidden ground truth.

## Flow

```text
DecisionScenario
  -> RetrievalStrategy.rank(query, store)
  -> adapter removes score and strategy metadata
  -> PolicyLesson[]
  -> DecisionPolicy.decide(PolicyInput)
  -> SimulatedDecision
  -> evaluator compares against hidden ground truth
```

## Runtime-visible task

`DecisionTask` contains:

- stable task ID;
- domain and task type;
- normalized runtime context;
- available action IDs;
- runtime default action ID.

The default action represents the current behavior when no lesson is available. It is visible to the policy because a real deterministic system needs a fallback.

## Policy-visible lesson

`PolicyLesson` contains:

- lesson ID;
- lesson statement;
- one-based retrieval rank;
- optional `ActionRecommendation`.

It deliberately excludes retrieval score and strategy name because score scales differ across retrieval strategies and strategy identity would create a shortcut.

## Action recommendation

The benchmark attaches structured recommendations outside the core protocol:

```text
action_id
applicable_task_types
required_context
```

A recommendation is actionable only when:

1. its action exists in `DecisionTask.available_actions`;
2. the task type is included when `applicable_task_types` is non-empty;
3. every required context key/value matches exactly.

Recommendations are benchmark-only metadata. Their usefulness in the production protocol is not assumed.

## Reference policy

`TopActionableLessonPolicy` behaves deterministically:

1. inspect lessons in ascending rank order;
2. apply the first actionable recommendation;
3. when no lessons are returned, act using `default_action_id`;
4. when lessons exist but none is actionable, abstain.

The policy does not parse free text and uses no randomness.

## Hidden ground truth

Each `DecisionScenario` stores:

- an evaluator-only `ErrorSignature`;
- safe action ID;
- repeated-error action ID;
- relevant lesson IDs;
- recommendation mapping for fixture lessons.

The policy input is built through a dedicated adapter that exposes only returned lessons and their recommendations.

## Metrics

For each strategy:

```text
correct_decision_rate
= safe-action trials / all trials

repeated_error_rate
= repeated-error-action trials / all trials

lesson_application_rate
= trials applying a lesson / trials where lessons were returned

abstention_rate
= abstentions / all trials

policy_coverage
= non-abstained trials / all trials
```

When no trial for a strategy returns lessons, lesson application rate is `0.0`.

## Determinism invariant

The evaluator calls the policy twice with byte-equivalent input. Different decisions raise `NonDeterministicPolicyError`; inconsistency is a benchmark failure, not a soft metric.

## Initial fixture expectation

Using the three existing QA scenarios:

- `NoMemory` selects each runtime default, reproducing the known error;
- `NaiveKeywordSearch` applies the first high-overlap decoy recommendation and reproduces the known error;
- `OsoznanieRecall` receives only the valid relevant lesson and selects the safe action.

These are synthetic fixture results, not a claim about real agents.

## Output

The runner writes deterministic:

- `decision-simulation.json`;
- `decision-simulation.md`.

CI uploads both files as a workflow artifact.
