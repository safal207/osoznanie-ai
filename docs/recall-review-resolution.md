# Recall Design Review Resolution

The Issue #5 design review raised five blocking questions. Their resolution is recorded here for auditability.

1. **TrustLevel source of truth** — the current model and Evidence schema both use `untrusted`, `reported`, and `verified`.
2. **Lesson tags** — `Lesson.scope` is now a strict `LessonScope` with `domain`, `task_types`, and `tags`, reflected in JSON Schema.
3. **Storage dependency** — recall uses constructor injection through `RecallEngine(store)` and the minimal `RecallStore` protocol.
4. **Domain-only matching** — rejected by two hard gates: exact domain match and `scope_match > 0.30`.
5. **Duplicate final score** — removed from `ScoreBreakdown`; `RecallResult.score` is authoritative.

Measured usefulness remains intentionally deferred until a `LessonApplication` record exists.
