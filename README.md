# Osoznanie AI

**An open experience, reflection, and evolving identity layer for persistent AI agents.**

> An agent should not only remember the human. It should remember who it became beside the human.

Most AI memory systems store facts and retrieve similar information. Osoznanie explores a different question:

**How can an agent turn its own history into auditable experience that changes future behavior?**

```text
Event → Decision → Outcome → Reflection → Lesson → Memory
                                      ↓
Authorization → DecisionTrace → Action → Outcome evidence
                                      ↓
                            evolving behavior and identity
```

## What Osoznanie is

Osoznanie is an early-stage open protocol and Python software layer for agents that need continuity across tasks, sessions, models, and environments.

It represents:

- events the agent participated in;
- decisions, outcomes, and feedback;
- reflections grounded in evidence;
- reusable lessons and success criteria;
- commitments and identity traits;
- versioned memory with provenance and lifecycle state;
- authorization decisions and the exact memory used for an action;
- immutable traces explaining why agent behavior changed.

## What Osoznanie is not

Osoznanie does not claim to create consciousness or sentience. It focuses on persistent, inspectable, and controllable behavioral continuity.

A static persona prompt is a costume. Osoznanie aims to make an agent's behavior traceable to its actual history.

## Current architecture

```text
Raw events
    ↓
semantic MemoryMutation proposal
    ↓
deterministic consolidation and immutable versioning
    ↓
atomic compare-and-swap memory commit
    ↓
bitemporal projection: as_of + known_at
    ↓
deny-by-default authorization and restricted read
    ↓
decision proposal
    ↓
immutable DecisionTrace persisted before dispatch
    ↓
transactional ActionIntent outbox
    ↓
leased worker + immutable started ActionAttempt
    ↓
protected input resolution + hash check + typed adapter
    ↓
atomic terminal ActionAttempt + outbox transition
    ↓
Outcome evidence and future learning
```

The model may propose meaning. Deterministic code controls versioning, authorization, persistence order, idempotency, dispatch leases, typed execution boundaries, and audit evidence.

## Implemented capabilities

- immutable protocol records and deterministic JSON serialization;
- versioned `MemoryObject` history with provenance and lifecycle status;
- deterministic consolidation with upsert, dispute, and revoke operations;
- atomic SQLite memory-head commits with compare-and-swap protection;
- bitemporal memory projection using effective time and knowledge time;
- deny-by-default access policies stored as versioned memory;
- restricted SQLite reads that do not deserialize denied payloads;
- immutable deterministic `DecisionTrace` chains;
- `LessonApplication`, `SuccessCriterion`, `OutcomeObservation`, and `CriterionEvaluation` contracts;
- mandatory audited orchestration with trace-before-action execution;
- durable `ActionIntent` outbox leasing and retry scheduling;
- immutable started and terminal `ActionAttempt` evidence;
- atomic terminal-attempt and outbox finalization;
- worker dispatch with protected input resolution, hash verification, and typed tool adapters;
- deterministic retry protection for already-persisted traces and terminal attempts;
- schema, lint, Python 3.11/3.12, test, and benchmark CI jobs.

## Installation

Osoznanie is currently an alpha project and is not presented as a stable PyPI release. Install it from source:

```bash
git clone https://github.com/safal207/osoznanie-ai.git
cd osoznanie-ai
python -m pip install -e ".[dev]"
pytest
```

Python 3.11 or newer is required.

## Public API quickstart

Core contracts are available from the package root:

```python
from datetime import UTC, datetime

from osoznanie import (
    AuditedDecisionRequest,
    AuthorizationQuery,
    DecisionProposal,
)

now = datetime.now(UTC)

request = AuditedDecisionRequest(
    authorization_query=AuthorizationQuery(
        requester_id="qa-agent",
        action="release.review",
        as_of=now,
        known_at=now,
        key_prefixes=["qa.lesson."],
    ),
    agent_id="qa-agent",
    decision_at=now,
)

proposal = DecisionProposal(
    action="release.review",
    alternatives_considered=["skip-regression"],
    reason_codes=["validated_prior_miss"],
    tool_name="test-runner",
    input_hash="sha256:your-input-hash",
)
```

A SQLite orchestration boundary can be wired from the same public namespace:

```python
from osoznanie import (
    AuditedDecisionOrchestrator,
    AuthorizationEngine,
    SQLiteAccessPolicyStore,
    SQLiteAuthorizedMemoryStore,
    SQLiteDecisionTraceStore,
    SQLiteExperienceStore,
)

store = SQLiteExperienceStore("osoznanie.db")

orchestrator = AuditedDecisionOrchestrator(
    authorization=AuthorizationEngine(SQLiteAccessPolicyStore(store)),
    memory_store=SQLiteAuthorizedMemoryStore(store),
    trace_store=SQLiteDecisionTraceStore(store),
    outcome_store=store,
)
```

Before `orchestrator.run(...)`, the database must contain committed memory and a governing access-policy memory. A complete runnable QA demonstrator is the next product milestone.

## Core protocol objects

### Experience and identity

- `Event`
- `Decision`
- `Outcome`
- `Reflection`
- `Lesson`
- `Commitment`
- `Trait`
- `Evidence`
- `IdentitySnapshot`

### Memory and application

- `MemoryObject`
- `MemoryMutation`
- `ConsolidationResult`
- `MemoryView`
- `LessonApplication`
- `SuccessCriterion`
- `OutcomeObservation`
- `CriterionEvaluation`

### Authorization, dispatch, and audit

- `AuthorizationQuery`
- `AccessPolicyContent`
- `AccessDecisionTrace`
- `DecisionProposal`
- `AuditedDecisionRequest`
- `AuditedDecisionResult`
- `DecisionTrace`
- `ActionIntent`
- `ActionAttempt`
- `ActionWorkerDispatcher`
- `ToolExecutionResult`

## Safety invariants

The audited decision and dispatch pipeline enforces these boundaries:

1. denied requests do not invoke the decision or action callback;
2. the decision callback receives only the authorized `MemoryView`;
3. the proposed action must equal the authorized action;
4. the initial immutable trace is persisted before dispatch;
5. trace persistence failure prevents the action;
6. raw protected tool input is not stored in `ActionIntent`;
7. a started `ActionAttempt` is persisted before the external adapter runs;
8. input hash and typed schema validation pass before tool invocation;
9. adapters never receive the raw lease token;
10. terminal attempt evidence and outbox state change commit atomically;
11. action failure does not rewrite prior evidence;
12. denied external results do not disclose protected keys or policy identifiers.

## Documentation

- [Manifesto](docs/manifesto.md)
- [Protocol v0.1](docs/protocol-v0.1.md)
- [Versioned Memory Object v0.1](docs/memory-object-v0.1.md)
- [Deterministic Consolidation Engine v0.1](docs/consolidation-engine-v0.1.md)
- [Atomic Memory Commits v0.1](docs/atomic-memory-commits-v0.1.md)
- [Bitemporal Memory View v0.1](docs/bitemporal-memory-view-v0.1.md)
- [Bitemporal Access Control v0.1](docs/bitemporal-access-control-v0.1.md)
- [Audited Decision Orchestrator v0.1](docs/audited-decision-orchestrator-v0.1.md)
- [Worker Dispatcher and Typed Tool Adapters v0.1](docs/worker-dispatcher-v0.1.md)

## First demonstrator

The first product use case is a persistent QA agent that can:

1. record a test decision and its result;
2. reflect on a missed defect or successful detection;
3. extract a reviewable lesson;
4. apply that lesson to a similar future release;
5. persist the exact authorization, memory, decision, action, and outcome trail;
6. explain why its behavior changed.

The primary success metric is not how many memories are stored. It is:

> **How many previously observed mistakes does the agent stop repeating because of validated experience?**

## Status

Osoznanie is in active alpha development. The core memory, authorization, application, audited-decision, durable dispatch, and typed worker contracts are implemented, but interfaces and schemas may still change before the first stable release.

The local execution layer provides durable at-least-once delivery and atomic evidence/outbox finalization. Distributed exactly-once behavior still depends on external providers honoring the supplied idempotency key.

## Vision

Osoznanie aims to become a portable experience layer independent of any single model provider or agent framework.

**The model may change. The agent's accountable history should not disappear with it.**
