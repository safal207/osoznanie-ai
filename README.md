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
immutable DecisionTrace persisted before action
    ↓
optional action execution
    ↓
optional Outcome and superseding DecisionTrace
```

The model may propose meaning. Deterministic code controls versioning, authorization, persistence order, idempotency, and audit evidence.

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
- deterministic retry protection for already-persisted traces;
- an executable audited QA learning-loop demonstrator;
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

Before `orchestrator.run(...)`, the database must contain committed memory and a governing access-policy memory.

## Runnable QA demonstrator

Run the complete local proof without an LLM, browser service, or external database:

```bash
python examples/qa_agent_demo.py
```

The demonstrator uses real SQLite stores and shows this sequence:

```text
missed Android Chrome defect
→ failed release outcome
→ validated reflection and lesson
→ behavioral-rule memory
→ governing allow policy
→ authorized lesson projection
→ trace v1 persisted
→ browser-device checks executed
→ defect prevented before release
→ successful outcome
→ superseding trace v2
```

The automated test verifies that the second release succeeds, the exact lesson and policy ids appear in trace v1, and the attached outcome creates trace v2.

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

### Authorization and audit

- `AuthorizationQuery`
- `AccessPolicyContent`
- `AccessDecisionTrace`
- `DecisionProposal`
- `AuditedDecisionRequest`
- `AuditedDecisionResult`
- `DecisionTrace`

## Safety invariants

The audited decision pipeline enforces these boundaries:

1. denied requests do not invoke the decision or action callback;
2. the decision callback receives only the authorized `MemoryView`;
3. the proposed action must equal the authorized action;
4. the initial immutable trace is persisted before external execution;
5. trace persistence failure prevents the action;
6. action failure does not rewrite the original trace;
7. an outcome creates a superseding trace rather than mutating history;
8. denied external results do not disclose protected keys or policy identifiers.

## Documentation

- [Manifesto](docs/manifesto.md)
- [Protocol v0.1](docs/protocol-v0.1.md)
- [Versioned Memory Object v0.1](docs/memory-object-v0.1.md)
- [Deterministic Consolidation Engine v0.1](docs/consolidation-engine-v0.1.md)
- [Atomic Memory Commits v0.1](docs/atomic-memory-commits-v0.1.md)
- [Bitemporal Memory View v0.1](docs/bitemporal-memory-view-v0.1.md)
- [Bitemporal Access Control v0.1](docs/bitemporal-access-control-v0.1.md)
- [Audited Decision Orchestrator v0.1](docs/audited-decision-orchestrator-v0.1.md)

## First demonstrator

The first product use case is now executable: a persistent QA agent can:

1. record a test decision and its failed result;
2. reflect on the missed defect;
3. extract a human-approved lesson;
4. store it as authorized behavioral memory;
5. apply that lesson to a similar future release;
6. persist the exact authorization, memory, decision, action, and outcome trail;
7. show that the previously escaped defect is prevented before release.

The primary success metric is not how many memories are stored. It is:

> **How many previously observed mistakes does the agent stop repeating because of validated experience?**

## Status

Osoznanie is in active alpha development. The core memory, authorization, application, and audited-decision contracts are implemented, and the repository includes one runnable end-to-end QA proof. Interfaces and schemas may still change before the first stable release.

The project does not yet guarantee distributed exactly-once tool execution. That requires a transactional outbox or an equivalent integration boundary.

## Vision

Osoznanie aims to become a portable experience layer independent of any single model provider or agent framework.

**The model may change. The agent's accountable history should not disappear with it.**
