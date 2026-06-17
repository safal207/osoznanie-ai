# Protocol v0.1 Recall Amendment

**Status:** Draft amendment to Osoznanie Protocol v0.1  
**Applies to:** deterministic scoped retrieval introduced by Issue #5

This amendment makes the retrieval contract machine-readable and closes fields that were previously implicit.

## 1. Lesson scope

`Lesson.scope` is now a typed object:

```json
{
  "domain": "quality-assurance",
  "task_types": ["checkout-release-validation"],
  "tags": ["checkout", "chrome", "release"]
}
```

Fields:

- `domain`: optional normalized domain identifier;
- `task_types`: normalized, deduplicated task identifiers;
- `tags`: normalized, deduplicated retrieval terms.

Unknown fields are rejected. The canonical contract is `schemas/lesson.schema.json`.

## 2. Evidence trust

The canonical trust values remain:

```text
untrusted
reported
verified
```

These values are defined by `TrustLevel` and `schemas/evidence.schema.json`.

## 3. Evidence access metadata

Evidence adds:

- `access_policy` as `AccessPolicy`;
- optional `owner_id`;
- optional `agent_id`;
- optional `tenant_id`.

Supported policy values are:

```text
private
owner-and-agent
relationship
team
organization
public
```

The v0 recall engine implements `private`, `owner-and-agent`, and `public`. Reserved policies deny access by default.

## 4. Recall request

The canonical request contract is `schemas/recall-query.schema.json` and includes:

- `agent_id`;
- `requester_id`;
- optional `tenant_id`;
- `domain`;
- `task_type`;
- `tags`;
- `risk_level`;
- `max_items`.

## 5. Recall response

Each result is validated by `schemas/recall-result.schema.json` and contains:

- one authoritative final `score`;
- component-only `score_breakdown`;
- enum-backed `reason_codes`;
- typed provenance references;
- deterministic explanation text.

## 6. Storage dependency

Retrieval is implemented through constructor injection:

```python
engine = RecallEngine(store)
results = engine.recall(query)
```

The engine depends on the `RecallStore` protocol rather than SQLite directly.

## 7. Eligibility clarification

A candidate must satisfy both scope gates:

```text
domain_match == 1.0
scope_match > 0.30
```

A domain-only match does not qualify for ranking.
