from datetime import UTC, datetime

import pytest

from osoznanie.decision_trace import DecisionTrace, TraceAuthorizationDecision
from osoznanie.decision_trace_store import SQLiteDecisionTraceStore
from osoznanie.storage import MissingReferenceError, SQLiteExperienceStore


def test_trace_store_rejects_missing_references() -> None:
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    trace = DecisionTrace(
        id="dtr_missing",
        requester_id="reader",
        agent_id="agent",
        action="report.generate",
        authorization_decision=TraceAuthorizationDecision.ALLOW,
        policy_memory_ids=["mem_missing"],
        as_of=now,
        known_at=now,
        decision_at=now,
        created_at=now,
    )
    store = SQLiteExperienceStore()
    with pytest.raises(MissingReferenceError):
        SQLiteDecisionTraceStore(store).save(trace)
    assert not store.exists(trace.id)
