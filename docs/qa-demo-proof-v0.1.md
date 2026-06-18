# Audited QA Demonstrator v0.1

## Purpose

The QA demonstrator is the first executable proof that an Osoznanie agent can use validated experience to change a later release decision while preserving the exact authorization, memory, action, and outcome trail.

## Demonstrated loop

```text
release 1 misses Android Chrome checkout defect
→ failed Outcome
→ human-approved Reflection and Lesson
→ behavioral-rule MemoryObject
→ exact-key allow policy for release.review
→ restricted MemoryView
→ DecisionProposal
→ immutable DecisionTrace v1
→ browser-device matrix release gate
→ successful prevention Outcome
→ immutable DecisionTrace v2
```

## Run

```bash
python examples/qa_agent_demo.py
```

The example runs entirely against an in-memory `SQLiteExperienceStore`. It requires no network, browser, LLM, or external service.

## Proof assertions

The automated test verifies that:

1. the prior release outcome is `failure`;
2. the later release outcome is `success`;
3. the orchestrator returns `outcome_traced`;
4. the applied checks come from authorized behavioral memory;
5. trace v1 references the exact lesson-memory id;
6. trace v1 references the exact governing policy-memory id;
7. the outcome creates trace v2 instead of mutating trace v1.

## Boundary

This is a deterministic product proof, not a live browser automation system. It intentionally excludes distributed exactly-once execution, agent-framework adapters, LLM inference, and a user interface.
