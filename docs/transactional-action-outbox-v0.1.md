# Transactional Action Outbox v0.1

**Status:** Experimental  
**Related issue:** #48

## Purpose

The action outbox closes the crash window between durable decision evidence and an
external tool call.

```text
BEGIN IMMEDIATE
  persist immutable DecisionTrace v1
  enqueue deterministic ActionIntent
COMMIT
        ↓
lease-based worker claim
        ↓
external dispatch with stable idempotency key
        ↓
complete or fail
```

Either the trace and intent both exist, or neither exists.

## Stored data

`ActionIntent` stores safe dispatch metadata only:

- trace id;
- requester and agent ids;
- authorized action;
- tool name and optional call id;
- input hash;
- stable idempotency key;
- lease and retry state.

Raw tool payloads, memory contents, policy ids, and private model reasoning are not
stored in the outbox.

## Deterministic identity

The intent id and idempotency key are derived from canonical SHA-256 content covering
the trace id and normalized dispatch metadata. Re-enqueueing the same trace and
proposal is idempotent. A conflicting intent for the same trace fails closed.

## Leasing

Workers claim one ready item under `BEGIN IMMEDIATE`.

A claim:

- changes status to `leased`;
- assigns a random lease token and worker id;
- sets an expiry;
- increments `attempt_count`.

A live lease cannot be stolen. Once expired, another worker may reclaim the item with
a new token. Completion and failure require the current unexpired token.

## Terminal and retry states

Completion requires an existing outcome record and is terminal.

Failure without `retry_at` is terminal. Failure with `retry_at` clears the lease and
returns the item to `pending`; it becomes visible only when the retry time arrives.

## Limits

This protocol prevents duplicate claims and provides a stable idempotency key. True
exactly-once side effects still require the external tool or adapter to honor that
key. A later dispatcher layer will define tool-specific adapters and immutable
action-attempt records.
