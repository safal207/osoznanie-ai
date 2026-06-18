# Bitemporal Memory View v0.1

**Status:** Experimental  
**Related issue:** #37

## Purpose

The memory repository stores immutable versions and one explicit head per logical
memory. A state projector answers a different question:

> Which version governed each memory key at a requested point in time?

The projector is deterministic, store-backed, and hard-gated. It does not perform
semantic similarity ranking.

## Two timelines

Osoznanie distinguishes two kinds of time.

### Effective time: `as_of`

When a memory claims to be valid in the represented world.

Examples:

- a permission became effective on June 1;
- a trip was completed on June 10;
- a temporary constraint expired on June 18.

This timeline is represented by `MemoryObject.valid_from` and `valid_until`.

### Knowledge time: `known_at`

Which versions had already been committed and were therefore available to the
agent at reconstruction time.

A correction can be committed on June 20 with `valid_from` June 5. It changes the
current retrospective understanding of June 10, but it must not rewrite a faithful
reconstruction of what the agent knew on June 10.

```text
as_of=June 10, known_at=June 10
→ reconstruct state using only knowledge committed by June 10

as_of=June 10, known_at=None
→ reconstruct June 10 using all knowledge committed today
```

In v0.1, SQLite's `records.updated_at` insertion timestamp is used as knowledge
time. The column name is historical: committed memory records are immutable and
are not updated in place.

## Usage

```python
from datetime import UTC, datetime

from osoznanie import (
    MemoryViewEngine,
    MemoryViewQuery,
    SQLiteExperienceStore,
    SQLiteMemoryViewStore,
)

store = SQLiteExperienceStore("osoznanie.db")
view_store = SQLiteMemoryViewStore(store)
engine = MemoryViewEngine(view_store)

view = engine.project(
    MemoryViewQuery(
        as_of=datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
        known_at=datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
        memory_keys=["permission.email.send"],
    )
)
```

## Governing-version algorithm

For every logical `memory_key`:

1. apply optional key and memory-type filters;
2. exclude versions committed after `known_at`, when provided;
3. exclude versions whose `valid_from` is after `as_of`;
4. select the highest remaining version number;
5. apply lifecycle hard gates to that selected version.

The selected node is the **governing version**.

## Hard gates and no fallback

A governing version is returned only when:

```text
status == active
AND
(valid_until is null OR as_of < valid_until)
```

If the governing version is:

- revoked;
- disputed;
- outdated;
- expired;

then the logical key is absent from active state.

The projector never searches backwards for an older active version. This prevents
revoked permissions, disputed facts, and expired constraints from being resurrected
by retrieval.

```text
v1 active:  email sending allowed
v2 revoked: permission withdrawn

project after v2
→ no active permission
→ never return v1
```

The legacy `resolve_active_memory()` helper follows the same governing-version-first
rule.

## Result contract

`MemoryView` contains:

- active `entries`, sorted by `memory_key`;
- hard-gate `rejections`, also sorted by key;
- normalized `as_of` and optional `known_at` timestamps;
- aggregate `filter_counts` explaining exclusions.

An admitted entry includes deterministic reason codes:

- `governing_version`;
- `effective_at_query`;
- `active_status`;
- `not_expired`;
- `known_by_cutoff`, when `known_at` is supplied.

## Read/write separation

The transactional `SQLiteExperienceStore` remains the write model responsible for:

- `BEGIN IMMEDIATE`;
- compare-and-swap checks;
- immutable insertion;
- head movement;
- uniqueness and provenance validation.

`SQLiteMemoryViewStore` is a read adapter. It joins `memory_versions` with
`records`, validates that relational indexes match immutable payloads, and supplies
committed versions to `MemoryViewEngine`.

This separation allows the read path to evolve into an indexed or materialized
projection without changing commit semantics.

## Safety properties

- a future-effective version does not hide the currently governing version;
- a later backdated correction can be excluded with `known_at`;
- revoked, disputed, outdated, and expired states are binary gates;
- an omitted entry cannot silently fall back to older state;
- duplicate `(memory_key, version)` claims are treated as ambiguous history;
- naive timestamps are rejected at the query boundary;
- SQLite knowledge timestamps are normalized as UTC.

## Current limitations

- no access-control or tenant gate is applied yet;
- no semantic relevance ranking is performed;
- `records.updated_at` should eventually be replaced or renamed as an explicit
  `committed_at` transaction-time field;
- contradiction adjudication and branch merging remain separate concerns;
- projection results are not yet recorded in decision traces.

## Follow-up

1. access-control gates for projected memory;
2. semantic recall over the active projected state;
3. context assembly combining memory state and validated lessons;
4. decision traces recording exact memory IDs used by an action.
