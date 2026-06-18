# Worker Dispatcher and Typed Tool Adapters v0.1

## Purpose

The worker dispatcher turns one durable `ActionIntent` into an audited external tool invocation without giving the model authority to mutate queue or evidence state.

```text
ActionIntent pending
    ↓ claim with lease
ActionAttempt started (persisted)
    ↓ resolve protected input
hash check + typed validation
    ↓ adapter.execute(...)
ActionAttempt succeeded / failed
    + atomic outbox transition
```

## Trust boundaries

`ActionIntent` stores dispatch metadata and `input_hash`, but never the raw protected payload. A `SecureToolInputResolver` obtains the payload from an external trusted store at execution time and returns an ephemeral `ResolvedToolInput`.

The dispatcher verifies that the resolver-provided hash exactly matches the hash captured in the immutable `DecisionTrace` and `ActionIntent`. It then validates the payload with the adapter's Pydantic `input_model` before any external call.

A tool adapter receives:

- the validated typed request;
- intent and trace identifiers;
- worker identifier;
- tool-call identifier;
- idempotency key;
- the persisted started-attempt identifier.

It does not receive the raw lease token. The dispatcher does not persist the resolved payload.

## Adapter result contract

Adapters return one `ToolExecutionResult`:

- `succeeded`: requires an already persisted `Outcome` identifier;
- `retryable_failure`: requires an error code and may provide `retry_after`;
- `permanent_failure`: requires an error code and is terminal.

Adapters and resolvers may alternatively raise `RetryableToolError` or `PermanentToolError`. Unexpected resolver and adapter exceptions are converted into safe generic retryable codes; exception messages are not persisted.

## Fail-closed behavior

The dispatcher records a started attempt before resolving input or invoking a tool. It then fails without calling the adapter when:

- no adapter is registered for `tool_name`;
- resolved input hash differs from the captured hash;
- typed input validation fails.

Terminal evidence and the matching outbox transition are committed by `SQLiteActionFinalizer` in one transaction.

## Retry behavior

Retryable failures clear the current lease, return the intent to `pending`, set `available_at`, and preserve immutable started/failed attempt evidence. A later claim increments `attempt_count` and creates a new attempt chain.

## Current guarantee boundary

The dispatcher provides durable at-least-once delivery with idempotency metadata, immutable attempt evidence, and atomic local finalization. It cannot guarantee distributed exactly-once execution when an external provider performs an action but the worker loses the response before local finalization. Tool adapters must therefore forward the supplied `idempotency_key` whenever the provider supports idempotent requests.
