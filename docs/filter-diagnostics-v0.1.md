# Filter Diagnostics v0.1

`osoznanie_recall` records aggregate exclusion counters during the same retrieval pass that produced the returned lessons. Retrieval is not replayed for audit generation.

Each candidate contributes to one counter only: the first gate that excluded it.

Counters:

- validation rejected;
- not yet effective;
- expired;
- domain mismatch;
- insufficient scope;
- access denied;
- below the risk threshold.

## Visibility

Public artifacts disclose non-sensitive counters. The access-denied count is always represented as redacted with a null value.

`redacted` means only that the value is not disclosed. It does not claim that hidden candidates exist.

Restricted synthetic audit may disclose the aggregate numeric count. It never contains identifiers, statements, scores, or provenance for filtered candidates.

Strategies without a structured eligibility pipeline use `filter_summary: null`; zero never means “not measured.”

## Invariants

`AuditedCount` enforces:

- disclosed requires a non-negative integer value;
- redacted requires a null value.

Invalid combinations are rejected by the Pydantic model.
