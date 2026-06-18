# Versioned Memory Object v0.1

**Status:** Experimental contract  
**Related issue:** #27

## Purpose

A `MemoryObject` is one immutable version in the history of a logical memory.
It does not replace the raw event log. It is a derived, inspectable state that
must remain traceable to the events that support it.

The contract is designed to prevent three common failures in long-term agent memory:

1. silently overwriting history;
2. treating unsupported inference as fact;
3. continuing to retrieve stale or revoked information.

## Core distinction

```text
Event = what was observed
MemoryObject = the current derived state supported by observations
```

A logical memory keeps the same `memory_key` across versions. Each version receives
a new `id`. Later versions point to earlier versions with `supersedes` instead of
rewriting them in place.

## Temporal update example

A user first plans a trip to Singapore:

```json
{
  "id": "mem_trip_planned",
  "type": "memory",
  "memory_key": "trip.singapore.status",
  "memory_type": "fact",
  "content": {"state": "planned"},
  "source_event_ids": ["evt_trip_plan"],
  "confidence": 0.9,
  "importance": 0.7,
  "valid_from": "2026-06-01T09:00:00Z",
  "valid_until": "2026-06-10T12:00:00Z",
  "status": "outdated",
  "supersedes": [],
  "contradicts": [],
  "created_at": "2026-06-01T09:00:00Z",
  "updated_at": "2026-06-10T12:00:00Z",
  "version": 1
}
```

After the trip is confirmed, the agent creates a new version:

```json
{
  "id": "mem_trip_completed",
  "type": "memory",
  "memory_key": "trip.singapore.status",
  "memory_type": "fact",
  "content": {"state": "completed"},
  "source_event_ids": ["evt_trip_completed"],
  "confidence": 0.98,
  "importance": 0.7,
  "valid_from": "2026-06-10T12:00:00Z",
  "valid_until": null,
  "status": "active",
  "supersedes": ["mem_trip_planned"],
  "contradicts": [],
  "created_at": "2026-06-10T12:00:00Z",
  "updated_at": "2026-06-10T12:00:00Z",
  "version": 2
}
```

Default retrieval selects version 2. Version 1 remains available for audit and
historical reconstruction.

Temporal validity uses an inclusive start and exclusive end:

```text
valid_from <= query_time < valid_until
```

When `valid_until` is `null`, the version has no scheduled end, but it may still be
excluded by a non-active lifecycle status.

## Revocation example

A user previously allowed an agent to send routine status emails:

```json
{
  "id": "mem_email_permission_v1",
  "type": "memory",
  "memory_key": "permission.email.status_updates",
  "memory_type": "constraint",
  "content": {"allowed": true},
  "source_event_ids": ["evt_permission_granted"],
  "confidence": 1.0,
  "importance": 1.0,
  "valid_from": "2026-06-01T09:00:00Z",
  "valid_until": null,
  "status": "revoked",
  "supersedes": [],
  "contradicts": [],
  "created_at": "2026-06-01T09:00:00Z",
  "updated_at": "2026-06-18T12:00:00Z",
  "version": 1
}
```

The revocation event produces a new active version:

```json
{
  "id": "mem_email_permission_v2",
  "type": "memory",
  "memory_key": "permission.email.status_updates",
  "memory_type": "constraint",
  "content": {"allowed": false},
  "source_event_ids": ["evt_permission_revoked"],
  "confidence": 1.0,
  "importance": 1.0,
  "valid_from": "2026-06-18T12:00:00Z",
  "valid_until": null,
  "status": "active",
  "supersedes": ["mem_email_permission_v1"],
  "contradicts": [],
  "created_at": "2026-06-18T12:00:00Z",
  "updated_at": "2026-06-18T12:00:00Z",
  "version": 2
}
```

A revoked, outdated, disputed, or expired version must not be selected by default
active retrieval.

## Provenance rule

An active factual memory must contain at least one `source_event_id`. The model
rejects an active fact without provenance before it can be persisted.

The SQLite store additionally checks that every referenced event or memory already
exists. This prevents a memory from claiming provenance that is absent from the
local accountable history.

## Deterministic serialization

`MemoryObject.canonical_json()` sorts object keys and normalized reference lists.
The same validated object therefore produces the same UTF-8 JSON representation,
which can be hashed, signed, or included in an audit artifact.

## Current limitations

- The store validates reference existence but does not yet enforce that every
  `source_event_id` points specifically to an `Event` record.
- Conflict resolution is represented explicitly but not automatically adjudicated.
- Consolidation logic that proposes new versions is planned separately.
- Deletion propagation and recalculation of downstream memories remain future work.
