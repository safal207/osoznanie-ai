# QA Release Demonstrator

Run the first end-to-end Osoznanie product scenario from the repository root:

```bash
python -m examples.qa_release_demo
```

The demonstrator uses a real in-memory SQLite store and performs this sequence:

```text
prior QA failure event
  -> behavioral-rule MemoryObject
  -> versioned access-policy MemoryObject
  -> bitemporal authorization
  -> authorized memory projection
  -> DecisionProposal
  -> immutable DecisionTrace
  -> transactional ActionIntent
  -> leased strict worker
  -> typed qa.test_runner adapter
  -> immutable started/succeeded ActionAttempt chain
  -> atomic outbox completion
  -> defect Outcome with release_gate=blocked
  -> evidence-grounded Reflection
  -> active reusable Lesson
```

The protected test-runner payload includes a synthetic provider token. The resolver supplies it only in memory at execution time. The demo verifies that the token is absent from persisted record payloads and outbox state.

The tool execution itself succeeds even though the QA outcome has status `failure`: the runner worked correctly and discovered a release-blocking regression. This separates execution health from product quality outcome.
