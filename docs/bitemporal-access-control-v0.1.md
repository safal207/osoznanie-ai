# Bitemporal Access Control v0.1

**Status:** Experimental  
**Related issue:** #39

## Purpose

Memory projection must not reveal protected state before authorization. Access
policies are themselves versioned internal state, so they use the same effective
and knowledge timelines as ordinary memory.

```text
requester + action + as_of + known_at
                ↓
trusted access-policy projection
                ↓
deny-by-default authorization
                ↓
restricted SQLite read
                ↓
ordinary bitemporal MemoryView projection
                ↓
non-disclosing external result
```

## Policies as memory

Access policies are `MemoryObject` records with
`memory_type="access_policy"`.

```json
{
  "subject_id": "agent_reader",
  "action": "memory.read",
  "resource": {
    "kind": "exact_key",
    "value": "profile.private"
  },
  "effect": "allow"
}
```

Grant, update, dispute, expiration, and revocation therefore pass through the
existing mutation, consolidation, CAS commit, and bitemporal projection layers.
A revoked governing policy never falls back to an older allow.

## Root capability

Policy history is read through `SQLiteAccessPolicyStore`, a trusted adapter used by
`AuthorizationEngine`. Ordinary requesters never receive policy payloads and cannot
query the policy namespace through the external memory-view path.

`SQLiteAuthorizedMemoryStore` always excludes `MemoryType.ACCESS_POLICY`.

## External and internal contracts

### External

`AuthorizedMemoryViewEngine.project()` returns only a normal `MemoryView`.

For an absent key and a denied key, the external shape is identical:

```json
{
  "entries": [],
  "rejections": [],
  "filter_counts": {
    "filtered_by_key_or_type": 0,
    "not_known_by_cutoff": 0,
    "not_yet_effective": 0,
    "superseded_versions": 0,
    "non_active_governing": 0,
    "expired_governing": 0
  }
}
```

No protected key, memory id, access rejection, hidden count, or policy id appears.

### Internal audit

`AuthorizedMemoryViewEngine.audit()` returns `AccessDecisionTrace` through a
separate privileged interface. It may contain:

- requester and action;
- effective and knowledge timestamps;
- allow or deny decision;
- matched policy memory ids;
- internal reason codes;
- requested selectors.

The audit trace must never be embedded into the external `MemoryView`.

## Selector precedence

Policies support:

1. exact memory key;
2. key prefix;
3. memory type.

Higher specificity wins. At equal specificity, deny wins.

```text
allow prefix: profile.
deny exact:   profile.secret

profile.public → allow
profile.secret → deny
```

Authorization is deny-by-default when no governing policy matches.

## Bitemporal policy truth

Policy projection uses the same `as_of` and `known_at` as protected memory
projection.

```text
as_of=June 10, known_at=June 10
→ use only policies known by June 10

as_of=June 10, known_at=None
→ use today's policy knowledge about June 10
```

A grant committed June 20 with effective time June 5 does not retroactively appear
in an audit reconstruction whose `known_at` is June 10.

## Restricted SQLite read

The SQLite adapter first reads only relational identifiers and indexed JSON
metadata:

- memory id;
- memory key;
- version;
- memory type;
- commit timestamp.

`AuthorizedScope` decides whether the row is allowed before `_load_record()`
deserializes the protected payload. Denied payloads do not cross the read-adapter
boundary.

Requested exact-key, prefix, and memory-type selectors are also pushed into the SQL
query to reduce the candidate set.

## Fail-closed behavior

Authorization returns an empty external view when:

- no matching allow exists;
- an explicit deny governs the resource;
- the governing allow was revoked or otherwise hard-gated;
- policy history is ambiguous;
- an active policy payload is malformed.

Ambiguous policy history is recorded internally as
`policy_history_ambiguous`, but remains indistinguishable from absence externally.

## Current limitations

- requester identity is an input string, not a cryptographically verified identity;
- no role hierarchy or tenant inheritance exists;
- policy caching is not implemented;
- SQL examines JSON metadata because memory type is not yet a relational index
  column;
- audit traces are returned but not yet persisted as immutable decision records;
- policy-signing and root capability rotation remain future work.

## Follow-up

1. cryptographically signed requester/root capabilities;
2. tenant and relationship selectors;
3. cache keys containing policy head ids and versions;
4. immutable decision traces connecting policy ids, memory ids, and agent actions.
