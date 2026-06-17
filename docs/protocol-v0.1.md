# Osoznanie Protocol v0.1

**Status:** Draft  
**Scope:** Conceptual data model and lifecycle for experience-driven persistent AI agents.

This document defines the first draft of the Osoznanie protocol. It is intentionally implementation-neutral and may change substantially.

## 1. Goal

The protocol describes how an agent can transform interaction history into reviewable experience that may influence future behavior.

```text
Event → Decision → Outcome → Reflection → Lesson → IdentitySnapshot
```

The protocol must preserve provenance, uncertainty, reversibility, and human control.

## 2. Design principles

1. **Evidence before identity.** Adaptive traits should be supported by traceable events and validated lessons.
2. **Observation is not interpretation.** Raw records and generated reflections are separate objects.
3. **Proposals are not truth.** Reflections, lessons, and trait changes begin as proposals.
4. **Important change is versioned.** Identity updates create a new snapshot rather than overwriting history.
5. **Human control is mandatory.** Memories and derived objects can be inspected, corrected, rejected, exported, and deleted.
6. **Portability matters.** The protocol should not depend on one model, vector database, or agent framework.
7. **No sentience claim.** Identity means inspectable behavioral continuity, not consciousness.

## 3. Core entities

### 3.1 Event

A recorded occurrence involving the agent, a human, another agent, a tool, or the environment.

Required fields:

```json
{
  "id": "evt_...",
  "type": "event",
  "timestamp": "2026-06-17T12:00:00Z",
  "actor_ids": ["agent_qa", "human_alexey"],
  "context": {},
  "summary": "A checkout button failed on a subset of Chrome devices.",
  "evidence_ids": ["evd_..."],
  "sensitivity": "private"
}
```

An `Event` should describe what occurred without claiming why it occurred.

### 3.2 Decision

A choice made by the agent within an event or task.

```json
{
  "id": "dec_...",
  "type": "decision",
  "event_id": "evt_...",
  "agent_id": "agent_qa",
  "chosen_action": "Approve release after desktop Chrome smoke test.",
  "alternatives_considered": [
    "Run full browser-device matrix",
    "Delay release"
  ],
  "reasoning_summary": "Risk was judged low based on previous desktop results.",
  "evidence_ids": ["evd_..."],
  "confidence": 0.63
}
```

The protocol stores a concise reasoning summary, not hidden chain-of-thought.

### 3.3 Outcome

The observed result of a decision or action.

```json
{
  "id": "out_...",
  "type": "outcome",
  "decision_id": "dec_...",
  "status": "failure",
  "summary": "Checkout failed on Android devices running Chrome 124.",
  "impact": {
    "severity": "high",
    "affected_users": "subset"
  },
  "evidence_ids": ["evd_..."],
  "observed_at": "2026-06-17T14:30:00Z"
}
```

### 3.4 Reflection

A generated interpretation of why an outcome occurred and what may be learned from it.

```json
{
  "id": "ref_...",
  "type": "reflection",
  "source_ids": ["evt_...", "dec_...", "out_..."],
  "hypotheses": [
    {
      "statement": "The test scope excluded the affected mobile browser configuration.",
      "confidence": 0.92,
      "evidence_ids": ["evd_..."]
    }
  ],
  "limitations": [
    "Root cause is not yet confirmed by engineering logs."
  ],
  "validation_status": "proposed"
}
```

A reflection may contain multiple hypotheses and must preserve uncertainty.

### 3.5 Lesson

A reusable rule proposed from one or more reflections.

```json
{
  "id": "les_...",
  "type": "lesson",
  "statement": "Before approving checkout changes, test the supported browser-device matrix rather than desktop Chrome alone.",
  "scope": {
    "domain": "quality-assurance",
    "task_types": ["checkout-release-validation"]
  },
  "source_reflection_ids": ["ref_..."],
  "confidence": 0.88,
  "validation_status": "human_approved",
  "effective_from": "2026-06-17T15:00:00Z",
  "expires_at": null
}
```

A lesson should be narrow enough to test and revise.

### 3.6 Commitment

A promise, agreement, or expected future action involving the agent.

```json
{
  "id": "com_...",
  "type": "commitment",
  "agent_id": "agent_qa",
  "counterparty_ids": ["human_alexey"],
  "statement": "Include mobile Chrome coverage in the next checkout regression plan.",
  "created_from_ids": ["les_..."],
  "due_at": "2026-06-24T09:00:00Z",
  "status": "open",
  "completion_evidence_ids": []
}
```

### 3.7 Trait

A persistent but revisable tendency that contributes to the agent's working identity.

```json
{
  "id": "trt_...",
  "type": "trait",
  "name": "cross-platform caution",
  "description": "Prefers representative browser-device coverage for customer-critical flows.",
  "value": 0.72,
  "stability": "adaptive",
  "source_lesson_ids": ["les_..."],
  "confidence": 0.81,
  "validation_status": "human_approved"
}
```

Traits must not be inferred from a single weak event without explicit validation.

### 3.8 Evidence

A source that supports an event, outcome, reflection, lesson, or identity change.

```json
{
  "id": "evd_...",
  "type": "evidence",
  "source_type": "test-report",
  "uri": "internal://reports/checkout-2026-06-17",
  "content_hash": "sha256:...",
  "captured_at": "2026-06-17T14:35:00Z",
  "trust_level": "verified",
  "access_policy": "owner-and-agent"
}
```

### 3.9 IdentitySnapshot

A versioned view of the agent's current working identity.

```json
{
  "id": "ids_...",
  "type": "identity_snapshot",
  "agent_id": "agent_qa",
  "version": 4,
  "created_at": "2026-06-17T15:10:00Z",
  "core_constraints": [
    "Do not fabricate test evidence.",
    "Escalate unresolved high-severity risk."
  ],
  "active_trait_ids": ["trt_..."],
  "active_lesson_ids": ["les_..."],
  "open_commitment_ids": ["com_..."],
  "previous_snapshot_id": "ids_previous",
  "change_summary": "Cross-platform caution increased after validated checkout incident.",
  "approved_by": ["human_alexey"]
}
```

## 4. Object states

Derived objects should use explicit lifecycle states.

```text
proposed → reviewed → approved → active
                    ↘ rejected
active → deprecated → archived
```

Suggested values:

- `proposed`
- `machine_reviewed`
- `human_approved`
- `rejected`
- `active`
- `deprecated`
- `expired`

## 5. Experience lifecycle

### Step 1: Capture

Record the event, decision, evidence, and expected result.

### Step 2: Observe

Attach the actual outcome and impact.

### Step 3: Reflect

Generate bounded hypotheses with confidence and limitations.

### Step 4: Validate

Use human review, trusted automated checks, or additional evidence.

### Step 5: Extract lesson

Create a narrow, testable lesson with a defined scope.

### Step 6: Propose identity change

A lesson may propose changes to traits, commitments, or decision preferences.

### Step 7: Create snapshot

Approved changes produce a new `IdentitySnapshot`.

### Step 8: Apply and measure

When a similar situation occurs, retrieve relevant active lessons and record whether they improved the result.

## 6. Retrieval contract

Before an important decision, the agent may request an experience context.

Example request:

```json
{
  "agent_id": "agent_qa",
  "task": "Validate a checkout release for Chrome clients.",
  "risk_level": "high",
  "max_items": 10
}
```

Example response:

```json
{
  "relevant_lessons": ["les_..."],
  "open_commitments": ["com_..."],
  "relevant_traits": ["trt_..."],
  "source_evidence": ["evd_..."],
  "explanation": "The lesson is relevant because a previous checkout approval failed when mobile Chrome coverage was omitted."
}
```

Retrieval should combine semantic relevance with:

- task scope;
- recency;
- evidence trust;
- validation state;
- confidence;
- prior measured usefulness;
- privacy and access rules.

## 7. Identity update rules

The draft protocol separates identity into two layers.

### Core constraints

Stable rules that cannot be changed through ordinary automated reflection.

Examples:

- safety boundaries;
- legal or organizational constraints;
- explicit owner-defined values;
- non-deception requirements.

### Adaptive traits

Revisable working tendencies formed through experience.

Examples:

- preferred testing strategy;
- escalation threshold;
- communication style;
- tolerance for uncertainty;
- collaboration habits.

An identity update should be rejected when:

- evidence is missing or untrusted;
- the lesson conflicts with a core constraint;
- the source appears manipulated;
- the proposed trait is too broad for the supporting experience;
- the change would expose private information to another context;
- approval is required but absent.

## 8. Forgetting and correction

The system must support:

- deleting raw events when legally or personally required;
- invalidating derived reflections and lessons;
- recalculating affected identity snapshots;
- expiring time-sensitive lessons;
- deprecating obsolete beliefs;
- rolling back to a previous snapshot;
- exporting the complete provenance chain.

Deletion should propagate through derived objects without silently leaving unsupported traits active.

## 9. Security considerations

Persistent memory creates a new attack surface.

Initial threats include:

- memory poisoning;
- forged outcomes;
- prompt injection stored as experience;
- privilege leakage between users;
- identity drift caused by repeated manipulation;
- sensitive autobiographical data exposure;
- malicious or accidental deletion of provenance.

Mitigations should include:

- evidence trust levels;
- source authentication;
- content hashing;
- tenant isolation;
- write permissions;
- approval gates;
- immutable audit records where appropriate;
- quarantining untrusted memories;
- conflict and contradiction detection.

## 10. Minimal API surface

A first implementation may expose:

```text
POST   /events
POST   /decisions
POST   /outcomes
POST   /reflections
POST   /lessons
POST   /commitments
POST   /identity/proposals
GET    /identity
GET    /identity/history
POST   /experience/query
GET    /memory/{id}/explain
PATCH  /memory/{id}
DELETE /memory/{id}
```

## 11. Initial evaluation metrics

A useful implementation should measure:

- repeated-error rate;
- commitment completion rate;
- false-memory rate;
- unsupported-trait rate;
- human correction frequency;
- performance improvement on recurring tasks;
- explanation completeness;
- identity consistency across model changes;
- successful rollback and deletion propagation.

Primary draft metric:

> How many previously observed mistakes does the agent stop repeating because of validated experience?

## 12. Open questions

- What level of evidence is sufficient to create a trait?
- When should identity changes require human approval?
- How should contradictory lessons be resolved?
- Which memories should decay automatically?
- How should relationship-specific identity remain isolated across people and organizations?
- How can an identity remain portable without leaking private history?
- How should lessons transfer between agents without copying personality?
- What benchmark can distinguish genuine experience use from retrieval imitation?

## 13. Planned next draft

Protocol v0.2 should add:

- machine-readable JSON Schemas;
- conflict-resolution rules;
- privacy and consent fields;
- relationship-memory boundaries;
- identity-drift thresholds;
- benchmark scenarios for a persistent QA agent.
