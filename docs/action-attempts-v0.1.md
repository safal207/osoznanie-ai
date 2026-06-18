# Immutable Action Attempts v0.1

Each real dispatch is an immutable two-record chain:

```text
started -> succeeded | failed
```

The started record is saved before the tool call. The terminal record adds finish time,
latency, outcome or safe error code, and an optional response hash.

Only safe metadata is stored. Raw payloads, memory content, policy ids, private reasoning,
and raw lease tokens are excluded.

IDs are derived from canonical SHA-256 content. Duplicate identical saves are idempotent;
conflicting revisions are rejected by SQLite constraints.

Third-party tools must still honor the outbox idempotency key for exactly-once external
side effects.
