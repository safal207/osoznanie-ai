# Recall Ranking Policy v0.2

## Purpose

`recall-ranking-v0.2` removes duplicated ranking influence from confidence and recency.

The scoring formula `scoring-v0.1` already contains:

```text
0.55 * scope_match
+ 0.20 * confidence
+ 0.15 * evidence_trust
+ 0.10 * recency
```

Therefore confidence and `effective_from` must not be applied again as secondary sort keys.

## Ordering contract

```text
public_score
-> deterministic score bucket
-> score_bucket DESC
-> lesson_id ASC
```

The tie-break field is deliberately neutral. It provides reproducibility without adding another substantive ranking signal.

## Score bucket

The active bucket width is:

```text
0.000001
```

This is a compatibility resolution because the existing public `RecallResult.score` is rounded to six decimal places. It is not a claim that score differences below this width are semantically equivalent.

The bucket is derived from the already rounded public score using decimal arithmetic. The public score field and its existing serialization remain unchanged in this release.

## Version binding

```text
ranking policy: recall-ranking-v0.2
score formula:  scoring-v0.1
```

The engine obtains the active policy through the registered policy map and rejects unknown policy IDs or a policy bound to a different active score formula.

## Determinism guarantees

- confidence is not used twice;
- recency is not used twice;
- storage insertion order does not affect ranking;
- equal score buckets are ordered by `lesson_id ASC`;
- ranking behavior is reproducible across repeated calls.

## Out of scope

This policy does not define restricted audit artifacts, filter counters, synthetic benchmark claims, or public/private trace visibility. Those contracts are versioned separately.
