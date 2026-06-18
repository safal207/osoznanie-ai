# ADR: LessonApplication Causal Discipline v0.1

**Status:** Accepted architectural direction; implementation deferred  
**Related issue:** #29  
**Scope:** architecture and invariants only

## Context

Osoznanie can retrieve a lesson and show that a decision followed it, but this is not yet enough to measure whether the lesson worked.

Three different facts must not be collapsed:

```text
lesson was retrieved
lesson was actually applied
an observed outcome followed the application
```

Even when a predefined success criterion is met, temporal order alone does not prove that the lesson caused the outcome.

This ADR defines the minimum causal and audit discipline required before introducing `LessonApplication`, outcome aggregation, maturity assessment, or contextual scoring.

It does not add production classes, JSON Schema fields, migrations, ranking weights, or maturity values.

## Decision summary

1. A success criterion is fixed no later than creation of the `RecallQuery`.
2. Retrieval, lesson selection, action execution, outcome observation, criterion evaluation, and causal attribution are separate records or stages.
3. A `LessonApplication` is created only when an action is actually initiated using a lesson. Retrieval or recommendation alone is not application.
4. Application records contain immutable references to the exact query, retrieval execution, lesson, action execution, criterion, and environment snapshot.
5. Outcome observations are append-only facts. Corrected observations or evaluations supersede earlier records instead of rewriting them.
6. Criterion result and causal attribution are independent dimensions.
7. Causal attribution defaults to uncertain.
8. Maturity is a future derived assessment, not a mutable field on `Lesson`.
9. Maturity is contextual: per-environment assessment precedes cross-environment aggregation.
10. Environment Graph and projection contracts remain future work and are referenced here only as architectural dependencies.

## Record boundaries

### Event and MemoryObject

`LessonApplication` belongs to the accountable event history. It records what was done.

`MemoryObject` is a derived, versioned state supported by events. It may later reference application and outcome records, but it must not replace or silently rewrite them.

```text
Event / LessonApplication / OutcomeObservation = what happened
MemoryObject / MaturityAssessment = derived interpretation
```

### Retrieval is not application

A lesson may be:

- eligible;
- retrieved;
- ranked;
- selected by a policy;
- presented to an actor;
- ignored;
- applied.

Only the final state creates a `LessonApplication`.

Using retrieval count or selected-lesson count as application count is prohibited.

## Temporal contract

The required order is:

```text
success criterion fixed
→ RecallQuery created
→ retrieval executed
→ lesson selected
→ action initiated
→ outcome observed
→ criterion evaluated
→ attribution assessed
```

The minimum temporal invariants are:

```text
success_criterion.fixed_at <= recall_query.created_at
recall_query.created_at <= retrieval_execution.completed_at
retrieval_execution.completed_at <= lesson_application.applied_at
lesson_application.applied_at <= outcome_observation.observed_at
outcome_observation.observed_at <= criterion_evaluation.evaluated_at
```

A system may omit an outcome or evaluation when it is not yet available, but it must not reverse these relationships.

Clock uncertainty, imported records, and distributed timestamps must be represented explicitly rather than repaired silently.

## SuccessCriterion

A criterion is part of the query-time decision contract, not a post-hoc explanation.

It must fix at least:

- a stable criterion identifier;
- definition version;
- evaluator version or evaluator type;
- observation window;
- measured field or event type;
- comparison rule and threshold;
- handling of missing observations;
- fixation timestamp.

Changing any semantic component creates a new criterion version or identifier.

The query stores an immutable reference to the fixed criterion. An evaluator must not choose or rewrite the criterion after seeing the outcome.

### Criterion result

The minimum result states are:

```text
met
not_met
indeterminate
```

`indeterminate` includes missing, late, inaccessible, conflicting, or insufficient observations. It is not equivalent to `not_met`.

## LessonApplication

The following shape is illustrative and non-normative. It must not be exported as a public schema from this ADR.

```python
class LessonApplication:
    application_id: str
    lesson_id: str
    recall_query_id: str
    retrieval_execution_id: str
    action_execution_id: str
    success_criterion_id: str
    environment_snapshot_id: str
    environment_projection_id: str | None
    applied_at: datetime
    actor_id: str | None
    idempotency_key: str
```

### Required semantics

- One record represents one lesson applied to one action execution.
- Several lessons used in the same action create separate application records sharing the same `action_execution_id`.
- Multiple simultaneous lessons are a confounder; no individual lesson receives causal credit by default.
- Re-delivery with the same idempotency key and identical payload is idempotent.
- Re-delivery with the same idempotency key and different payload is rejected.
- References are immutable after persistence.
- A revoked or inaccessible referenced object remains historically referential but may be redacted from a projection.

## OutcomeObservation

An outcome observation records measured facts, not whether the lesson deserves credit.

An observation must include:

- a stable observation identifier;
- the related action execution or application identifiers;
- observation timestamp;
- observation-source provenance;
- measured values or typed event reference;
- collection policy version;
- access classification.

Observations are append-only. A correction creates a new observation that supersedes the incorrect one.

Absence of an observation is not a negative outcome.

## CriterionEvaluation

Evaluation applies the already fixed criterion to accessible observations.

It records:

- criterion identifier and definition version;
- observation identifiers used;
- evaluator version;
- result: `met`, `not_met`, or `indeterminate`;
- evaluation timestamp;
- reason codes for indeterminate results;
- superseded evaluation identifier when corrected.

Evaluation must be reproducible from the referenced criterion and observations.

## Causal attribution

Criterion success is not causal proof.

```text
criterion_met ≠ lesson_caused_success
criterion_not_met ≠ lesson_caused_failure
```

The minimum attribution states are:

```text
not_assessed
uncertain
supported_by_controlled_comparison
contradicted_by_controlled_comparison
```

`uncertain` is the default for ordinary applications.

Moving to a controlled-comparison state requires a separately versioned evaluation design that defines, at minimum:

- comparison or control construction;
- assignment mechanism;
- confounder handling;
- sample inclusion rules;
- time window;
- analysis policy;
- uncertainty reporting.

This ADR does not define that design.

## Environment context

A flat environment string is insufficient for future applicability and maturity.

This ADR adopts three architectural principles:

1. Environment is a graph, not a flat attribute bag.
2. An interface between environments is a first-class entity.
3. Applicability depends on a deterministic projection of the relevant subgraph.

Additional future constraints are reserved:

- containment relations form a DAG;
- interaction relations may be cyclic;
- state and phase are separate from environment identity;
- projection policy is versioned and deterministic;
- access is checked before traversal or dereferencing;
- inaccessible nodes, relations, interfaces, states, identifiers, and counts do not enter traversal frontiers or intermediate diagnostics;
- condition expressions use an explicit recursive discriminated AST rather than an ambiguous attribute dictionary.

No `EnvironmentNode`, `EnvironmentInterface`, condition-language, snapshot, or projection production class is introduced by this ADR.

### Snapshot and projection references

A `LessonApplication` references the immutable environment snapshot observed for the action.

When the future projection protocol exists, it may also reference the exact projection and projection-policy version used for applicability or maturity analysis.

The snapshot remains the historical observation. The projection is a reproducible derived view.

## Maturity boundary

Maturity is not part of `ValidationStatus` and does not create a second lifecycle for a lesson.

It is a future derived assessment based on application, observation, evaluation, attribution, and environment records.

The conceptual identity of a per-environment assessment is:

```text
lesson_id
+ environment_projection_id
+ maturity_policy_version
```

A cross-environment aggregate uses `environment_projection_id = None` only as a distinct aggregate record. `None` must not mean that environment information was absent.

Cross-environment maturity is computed from per-environment assessments, not by pooling all raw applications into one count.

This preserves distinctions such as:

- stable performance across environments;
- strong performance in two environments and poor performance in a third;
- evidence from only one environment;
- unknown transferability;
- contradictory outcomes across phases or interfaces.

### Unknown is not low

Future assessment must distinguish at least:

```text
unknown
insufficient_evidence
measured_low
measured_medium
measured_high
```

A missing assessment must never be encoded as score zero.

No geometric aggregation is adopted until these semantics, data sources, and measurement policies exist.

## Privacy and access boundary

Access checks occur before resolving or traversing referenced objects.

The system must not first load restricted lesson, environment, interface, state, criterion, observation, or outcome data and redact it later.

A denied object must not enter:

- traversal frontier;
- visited set;
- intermediate projection;
- diagnostic identifier list;
- public count;
- public manifest entry;
- log message lacking restricted access controls.

Public projections may expose only fields explicitly allowed by a versioned projection policy. Even opaque identifiers can disclose relationships and therefore require access classification.

Historical referential integrity and public visibility are separate concerns: a restricted reference may remain valid in storage while being absent or redacted in a public projection.

## Immutability and correction

The following records are append-only after acceptance:

- fixed success criterion version;
- RecallQuery criterion reference;
- retrieval execution snapshot;
- LessonApplication;
- OutcomeObservation;
- CriterionEvaluation;
- AttributionAssessment.

Corrections create new records with `supersedes` references. They do not mutate the historical record that informed an earlier decision.

Deterministic canonical serialization and artifact integrity should be applied when these records become production contracts.

## Non-goals

This ADR does not:

- implement `LessonApplication`;
- modify `RecallQuery` schema;
- add a maturity field or null stub to `Lesson`;
- define Environment Graph production models;
- define a complete causal-inference method;
- claim that synthetic criterion success predicts production impact;
- change current recall scoring, ranking, risk thresholds, or filter gates;
- adopt geometric mean weights;
- infer application from retrieval or policy selection;
- automatically resolve conflicting outcomes.

## Rejected alternatives

### Criterion chosen after outcome observation

Rejected because it permits post-hoc confirmation and makes evaluation non-reproducible.

### Mutable LessonApplication with outcome fields added later

Rejected because later mutation erases what was known at application time. Outcome and evaluation are separate append-only records.

### Maturity scalar stored directly on Lesson

Rejected because maturity is derived, policy-versioned, environment-dependent, and recalculable.

### One pooled maturity count across environments

Rejected because it averages incomparable contexts and hides environment-specific failure.

### Flat environment fingerprint as the complete context

Rejected because nested environments, interfaces, phases, and interaction paths affect applicability.

### Geometric mean immediately added to current score

Rejected because current inputs are not independent, many required observations do not exist, and unknown values are not equivalent to zero.

### Retrieved or selected lessons counted as applied

Rejected because it inflates application evidence without proof that the action used the lesson.

## Consequences

### Positive

- prevents post-hoc criterion fitting;
- keeps observation, evaluation, and attribution auditable;
- preserves uncertainty instead of manufacturing causal claims;
- supports future per-environment learning without schema migration pressure today;
- aligns with immutable audit artifacts and versioned memory objects;
- avoids double-counting existing ranking signals.

### Costs

- more event types and references;
- delayed outcomes require asynchronous append-only observations;
- distributed timestamps and missing data require explicit handling;
- controlled attribution requires additional experimental design;
- environment snapshots and projections add future storage and privacy complexity.

## Implementation roadmap

1. Merge this ADR without production types.
2. Define versioned `SuccessCriterion`, `LessonApplication`, `OutcomeObservation`, and `CriterionEvaluation` contracts in a separate issue.
3. Add append-only persistence, idempotency, temporal validators, and canonical serialization.
4. Add deterministic synthetic fixtures for met, not-met, indeterminate, delayed, corrected, and inaccessible outcomes.
5. Collect application data without deriving maturity.
6. Define Environment Graph, snapshot, interface, condition AST, and projection-policy ADR.
7. Implement per-environment empirical assessment with explicit evidence and attribution quality.
8. Implement cross-environment aggregation from per-environment assessments.
9. Only then evaluate geometric or other non-compensatory aggregation policies.

## Decision boundary

The key rule is:

> A lesson becomes evidence-bearing only when its actual application is recorded against a criterion fixed before retrieval, in an immutable observed environment, while success and causal attribution remain separate claims.
