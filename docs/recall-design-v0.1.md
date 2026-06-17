# Deterministic Scoped Recall v0.1

**Status:** implementation design for Issue #5

## Goal

Before an important decision, an agent should retrieve only the validated experience that is relevant, accessible, and strong enough for the task.

The pipeline is deliberately deterministic:

```text
candidate lessons -> eligibility gates -> scoring -> threshold -> stable sort -> max_items
```

No LLM participates in filtering, scoring, authorization, reason-code generation, or explanation text.

## Query contract

A recall query contains:

- `agent_id`
- `requester_id`
- optional `tenant_id`
- normalized `domain`
- normalized `task_type`
- normalized, deduplicated `tags`
- `risk_level`
- `max_items` from 1 to 50

## Scope model

Lessons use these keys inside `scope`:

```json
{
  "domain": "quality-assurance",
  "task_types": ["checkout-release-validation"],
  "tags": ["checkout", "chrome", "release"]
}
```

Unknown or malformed values contribute zero.

## Scope score

```text
domain_match = 1 when domains are equal, otherwise 0
task_match   = 1 when query.task_type is in lesson task_types, otherwise 0
tag_match    = Jaccard(query tags, lesson tags)

scope_match = 0.30 * domain_match
            + 0.50 * task_match
            + 0.20 * tag_match
```

The scope eligibility gate is:

```text
scope_match >= 0.30
```

Therefore an exact domain match can admit a candidate to ranking, while task-type match remains the strongest scope signal.

## Eligibility gates

A lesson is eligible only when all conditions are true:

1. status is `human_approved` or `active`;
2. `effective_from <= now`;
3. `expires_at` is absent or later than `now`;
4. every linked evidence record is accessible to the query context;
5. `scope_match >= 0.30`.

A denied candidate is not scored and does not appear in explanations.

## Evidence traversal

For v0, evidence is collected through:

```text
Lesson -> source Reflection -> Hypothesis.evidence_ids -> Evidence
```

Evidence IDs are deduplicated. Missing or wrong-type references produce no trust value and should not crash recall.

## Access policy

Supported policies:

- `public`: visible to every query;
- `private`: visible only when `requester_id == owner_id`;
- `owner-and-agent`: visible when the requester is the owner or the query agent matches `evidence.agent_id`.

Reserved policies (`relationship`, `team`, `organization`) are deny-by-default in v0.

When `evidence.tenant_id` is set, the query tenant must match before policy evaluation. Missing ownership metadata denies non-public evidence.

## Evidence trust

```text
untrusted = 0.0
reported  = 0.5
verified  = 1.0
```

The lesson evidence score is the arithmetic mean of unique linked evidence scores. No linked evidence produces `0.0`.

## Recency

Recency uses exponential decay with a 365-day half-life:

```text
age_days = max(0, now - effective_from)
recency = exp(-ln(2) * age_days / 365)
```

This yields approximately 1.0 now, 0.5 after one year, and 0.25 after two years.

## Final score

```text
score = 0.55 * scope_match
      + 0.20 * lesson.confidence
      + 0.15 * evidence_trust
      + 0.10 * recency
```

## Risk thresholds

```text
low    = 0.35
medium = 0.45
high   = 0.55
```

Risk does not change evidence or confidence values. It changes the minimum final score required to influence the task.

## Stable ordering

Results are sorted by:

1. final score descending;
2. lesson confidence descending;
3. `effective_from` descending;
4. lesson ID ascending.

Then `max_items` is applied.

## Result contract

Each result contains:

- lesson ID and statement;
- final score;
- complete score breakdown;
- typed `ReasonCode` values;
- typed provenance references;
- deterministic explanation text.

The same stored records, query, and `now` value must produce byte-equivalent JSON output.

## Deferred work

Measured usefulness is deferred until `LessonApplication` exists. Embeddings, BM25, fuzzy domain matching, and LLM-generated explanations are explicitly outside v0.
