# Osoznanie AI

**An open experience, reflection, and evolving identity layer for persistent AI agents.**

> An agent should not only remember the human. It should remember who it became beside the human.

Most AI memory systems store facts and retrieve similar information. Osoznanie is exploring a different question:

**How can an agent turn its own history into auditable experience that changes future behavior?**

```text
Event → Decision → Outcome → Reflection → Lesson → Identity Change
```

## What Osoznanie is

Osoznanie is an early-stage open protocol and software layer for agents that need continuity across tasks, sessions, models, and environments.

It is designed to represent:

- events the agent participated in;
- decisions the agent made;
- outcomes and feedback;
- reflections grounded in evidence;
- reusable lessons;
- commitments to people or systems;
- identity traits formed through validated experience;
- a versioned history of how the agent changed.

## What Osoznanie is not

Osoznanie does not claim to create consciousness or sentience. It focuses on persistent, inspectable, and controllable behavioral continuity.

A static persona prompt is a costume. Osoznanie aims to make an agent's behavior traceable to its actual history.

## Core principle

```text
History + Memory + Reflection + Choice = Evolving Individuality
```

Individuality should not be an opaque profile generated once. Each adaptive trait should be linked to evidence, confidence, validation status, and a change history.

## Initial protocol objects

- `Event`
- `Decision`
- `Outcome`
- `Reflection`
- `Lesson`
- `Commitment`
- `Trait`
- `Evidence`
- `IdentitySnapshot`
- `MemoryObject`
- `MemoryMutation`
- `ConsolidationResult`

## Memory consolidation boundary

```text
Raw events → semantic proposal → validated MemoryMutation
          → deterministic ConsolidationEngine → MemoryObject version
```

The semantic layer may propose a change. The deterministic layer applies versioning,
provenance, lifecycle state, and supersession without silently rewriting history.

See:

- [Manifesto](docs/manifesto.md)
- [Protocol v0.1](docs/protocol-v0.1.md)
- [Versioned Memory Object v0.1](docs/memory-object-v0.1.md)
- [Deterministic Consolidation Engine v0.1](docs/consolidation-engine-v0.1.md)

## First demonstrator

The first planned use case is a persistent QA agent that can:

1. record a test decision and its result;
2. reflect on a missed defect or successful detection;
3. extract a reviewable lesson;
4. apply that lesson to a similar future release;
5. explain exactly why its behavior changed.

The primary success metric is not how many memories are stored. It is:

> **How many previously observed mistakes does the agent stop repeating because of validated experience?**

## Status

Osoznanie is currently in the protocol-definition stage. Interfaces and schemas may change substantially before the first stable release.

## Vision

Osoznanie aims to become a portable experience layer that remains independent of any single model provider or agent framework.

The model may change. The agent's accountable history should not disappear with it.
