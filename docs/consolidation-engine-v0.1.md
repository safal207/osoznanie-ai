# Deterministic Consolidation Engine v0.1

**Status:** Experimental  
**Related issue:** #30

## Purpose

The Consolidation Engine turns a normalized, source-backed memory mutation into the
next immutable `MemoryObject` version.

It is intentionally separate from semantic extraction:

```text
Conversation / tool output / environment event
                    ↓
      semantic proposal generation
                    ↓
          validated MemoryMutation
                    ↓
    deterministic Consolidation Engine
                    ↓
       immutable MemoryObject version
```

An LLM may propose a mutation. It does not receive authority to silently rewrite
memory history. The deterministic layer validates version progression, provenance,
type continuity, lifecycle state, and supersession.

## Mutation kinds

### `upsert`

Creates a new active logical memory or the next active version of an existing one.

### `dispute`

Creates a disputed version. It requires existing history and may link external
conflicting memory records through `contradicts`.

### `revoke`

Creates a revoked version. It requires existing history and is excluded from active
retrieval.

## Version rules

1. A new logical memory starts at version 1.
2. Later mutations use the same `memory_key`.
3. The latest version is selected by version number.
4. Version N+1 explicitly points to version N through `supersedes`.
5. Previous objects are never modified in place.
6. Multiple different records claiming the same key and version are rejected as
   ambiguous history.
7. A logical memory cannot change its `memory_type` after version 1.

## Deterministic identity

The generated memory id is derived from a SHA-256 digest of the canonical mutation
payload plus the previous memory id and version.

Canonical inputs include:

- mutation kind;
- logical memory key and type;
- content;
- sorted source event ids;
- confidence and importance;
- UTC-normalized effective time and optional validity end;
- sorted contradiction links;
- previous memory id and version.

Equivalent timestamps are normalized to UTC before hashing. Reprocessing the same
validated mutation against the same history therefore generates the same memory id
and equivalent consolidation result.

## Example: temporal update

```python
from datetime import UTC, datetime

from osoznanie import (
    ConsolidationEngine,
    MemoryMutation,
    MemoryType,
)

engine = ConsolidationEngine()

planned = engine.consolidate(
    MemoryMutation(
        memory_key="trip.singapore.status",
        memory_type=MemoryType.FACT,
        content={"state": "planned"},
        source_event_ids=["evt_trip_plan"],
        confidence=0.9,
        importance=0.7,
        effective_at=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
    )
).memory

completed = engine.consolidate(
    MemoryMutation(
        memory_key="trip.singapore.status",
        memory_type=MemoryType.FACT,
        content={"state": "completed"},
        source_event_ids=["evt_trip_completed"],
        confidence=0.98,
        importance=0.7,
        effective_at=datetime(2026, 6, 10, 12, 0, tzinfo=UTC),
    ),
    history=[planned],
).memory

assert completed.version == 2
assert completed.supersedes == [planned.id]
```

## Example: revocation

```python
from osoznanie import MemoryMutationKind

revoked = engine.consolidate(
    MemoryMutation(
        kind=MemoryMutationKind.REVOKE,
        memory_key="permission.email.status_updates",
        memory_type=MemoryType.CONSTRAINT,
        content={"reason": "user withdrew permission"},
        source_event_ids=["evt_permission_revoked"],
        confidence=1.0,
        importance=1.0,
        effective_at=datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
    ),
    history=[previous_permission],
).memory
```

The new version is retained for audit but is not eligible for default active
retrieval.

## Safety boundary

The engine does not decide whether an event *means* that a user preference, fact,
goal, or permission changed. It only applies a mutation that has already passed the
proposal and validation boundary.

This separation makes it possible to add later controls such as:

- human approval for high-impact mutations;
- evidence trust thresholds;
- permission-specific policies;
- tenant and relationship boundaries;
- idempotent event processing;
- optimistic concurrency when persisting a result.

## Current limitations

- The engine is pure and does not persist results atomically.
- It validates source ids structurally but storage remains responsible for checking
  that referenced records exist.
- It does not yet verify that every source id points specifically to an `Event`.
- It does not generate semantic proposals from raw event batches.
- It does not automatically adjudicate contradictions.
