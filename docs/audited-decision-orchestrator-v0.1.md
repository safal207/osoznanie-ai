# Audited Decision Orchestrator v0.1

**Status:** Experimental  
**Related issue:** #45

## Purpose

The audited decision orchestrator is the mandatory application boundary between
bitemporal authorization, protected memory projection, action selection, immutable
trace persistence, and optional action execution.

```text
AuthorizationQuery
        ↓
one captured AuthorizationResult
        ↓
restricted MemoryView
        ↓
DecisionCallback
        ↓
immutable DecisionTrace v1 persisted
        ↓
optional ActionExecutor
        ↓
optional Outcome persisted
        ↓
immutable DecisionTrace v2
```

The initial trace is always persisted before an external action can run.

## Confused-deputy prevention

`DecisionProposal.action` must exactly match `AuthorizationQuery.action`.

A caller cannot authorize one operation and then use the resulting memory context
to execute a different operation.

```text
authorized: report.generate
proposed:   email.send
→ DecisionProposalError
```

## Single captured authorization

The orchestrator calls `AuthorizationEngine.authorize()` once. The resulting policy
ids and `AuthorizedScope` are used both for restricted memory loading and for the
immutable trace.

Authorization is not re-resolved between decision selection and trace creation.
Later policy-head changes therefore cannot rewrite which policy version governed the
action.

## Callback boundary

The decision callback receives `DecisionContext`, containing:

- requester id;
- authorized action;
- `as_of` and `known_at`;
- externally safe authorized `MemoryView`.

It does not receive the policy store, unrestricted memory store, internal scope, or
policy provenance.

The callback returns a typed `DecisionProposal`. Reason codes and alternatives may
be captured, but private model chain-of-thought is outside the contract.

## Trace-before-action invariant

The execution order is fixed:

1. build deterministic trace v1;
2. persist trace v1;
3. only then invoke `ActionExecutor`.

If trace persistence fails, the executor is never called.

If action execution fails, `ActionExecutionError` contains the persisted trace id.
The trace remains immutable evidence that the action was authorized and attempted.

## Outcome attachment

An executor may return a typed `Outcome`.

The orchestrator then:

1. persists the outcome through `OutcomeSink`;
2. builds a new trace version with `outcome_id`;
3. persists the superseding trace.

The original trace is never changed. Outcomes observed before `decision_at` are
rejected.

## Idempotent retry

Decision traces have deterministic ids. Before saving trace v1, the orchestrator
checks whether it is already committed.

When the same captured request is retried and the trace already exists, the result
is `already_traced` and the executor is not called again.

This protects ordinary single-process retries. A transactional outbox is still
required for fully distributed exactly-once tool execution.

## Fail-closed defaults

- authorization deny returns an empty external view;
- denied requests never invoke the decision callback;
- empty authorized context returns `no_authorized_context` by default;
- callers must explicitly set `require_memory_context=false` for actions that do not
  depend on projected memory;
- invalid proposal type or action mismatch blocks trace creation;
- missing outcome persistence prevents creation of trace v2.

## Result privacy

`AuditedDecisionResult` contains no policy ids, internal authorization reasons,
protected selectors, or hidden-record counts.

It exposes only:

- authorization decision;
- external memory view;
- pipeline status;
- persisted trace ids;
- optional outcome id.

## Current limitations

- the action payload remains outside the protocol; only `input_hash` is captured;
- distributed exactly-once execution is not guaranteed;
- external action-attempt records are not yet append-only protocol objects;
- callbacks and executors run synchronously;
- outcome persistence may require the caller to create the referenced `Decision`
  record first.

## Follow-up

1. transactional outbox for tool execution;
2. immutable action-attempt records;
3. replay and incident reconstruction API;
4. signed trace envelopes and Merkle audit proofs.
