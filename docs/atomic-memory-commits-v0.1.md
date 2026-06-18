# Atomic Memory Commits v0.1

**Status:** Experimental  
**Related issue:** #34

## Purpose

The deterministic consolidation layer creates immutable memory versions. The
repository layer decides whether a generated version may become the next committed
head of a logical memory.

This layer uses compare-and-swap preconditions:

```python
store.commit_memory(
    result,
    expected_head_id="mem_previous",
    expected_version=7,
)
```

The expected head and version are command preconditions. They are not fields on the
immutable `MemoryObject` because they describe a write attempt, not historical
state.

## SQLite transaction contract

Every head-changing commit starts with:

```sql
BEGIN IMMEDIATE;
```

A deferred `BEGIN` would allow two writers to read the same head before either one
acquires the write lock. `BEGIN IMMEDIATE` acquires SQLite's reserved write lock
before the repository reads and validates the head.

Inside one transaction the repository:

1. reads the current row from `memory_heads`;
2. compares it with the expected head id and optional expected version;
3. validates exact version progression and supersession;
4. validates that every provenance reference already exists;
5. inserts the immutable protocol record;
6. inserts its `(memory_key, version)` index row;
7. moves the explicit memory head;
8. commits.

Any exception rolls back the record, version index, and head update together.

## Relational indexes

Protocol payloads remain in the generic `records` table. Two relational tables add
memory-graph invariants without duplicating JSON payloads.

```sql
CREATE TABLE memory_versions (
    memory_id TEXT PRIMARY KEY REFERENCES records(id),
    memory_key TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version >= 1),
    UNIQUE(memory_key, version)
);

CREATE TABLE memory_heads (
    memory_key TEXT PRIMARY KEY,
    current_id TEXT NOT NULL REFERENCES records(id),
    current_version INTEGER NOT NULL CHECK(current_version >= 1),
    updated_at TEXT NOT NULL
);
```

The unique constraint makes duplicate version claims a database-level failure, not
only an application convention.

## Error taxonomy

### `VersionConflictError`

The stored head no longer matches the caller's compare-and-swap precondition.
This is normal concurrency and may be handled by re-reading, reconsolidating, and
retrying with bounded backoff.

### `InvalidMemoryProgressionError`

The proposed result does not advance exactly from the stored head. Examples:

- version 4 attempts to follow version 2;
- `previous_memory_id` differs from the database head;
- `supersedes` does not contain exactly the current head;
- the memory type changes across versions.

### `AmbiguousMemoryHistoryError`

A committed history invariant is already broken or a database uniqueness constraint
reveals two different nodes claiming one logical version. This is not retryable and
should trigger investigation.

### `UnsafeMemoryWriteError`

A caller tries to persist version 2 or later through generic `save()` instead of the
atomic repository method.

## Idempotent delivery

Memory ids are deterministic. If a client retries a result that is already the
current head and the stored payload/version match exactly, the repository returns
the committed memory without creating another version.

This check occurs before stale expected-head comparison so delivery retries remain
safe after a successful commit whose response was lost.

## WAL mode

File-backed SQLite stores request WAL mode and set a busy timeout. Independent
writers therefore serialize at `BEGIN IMMEDIATE`; after the winner commits, a stale
writer acquires the lock, reads the new head, and receives `VersionConflictError`.

## Safety properties

- a logical memory has one explicit committed head;
- a `(memory_key, version)` pair identifies at most one node;
- a writer cannot silently overwrite another writer's update;
- provenance and head movement commit atomically;
- later memory versions cannot bypass repository validation;
- committed memory history cannot be physically deleted through the generic API;
- retries of the exact committed result are idempotent.

## Follow-up

- bounded retry helper with jitter;
- state projection at an arbitrary `as_of` time;
- branch and merge policies for conflicting evidence;
- retrieval traces connecting committed heads to agent decisions.
