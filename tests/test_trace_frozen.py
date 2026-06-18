from datetime import UTC, datetime

import pytest

from osoznanie.decision_trace import DecisionTrace, TraceAuthorizationDecision


def test_trace_is_frozen() -> None:
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    trace = DecisionTrace(
        id="dtr_frozen",
        requester_id="reader",
        agent_id="agent",
        action="report.generate",
        authorization_decision=TraceAuthorizationDecision.ALLOW,
        policy_memory_ids=["mem_policy"],
        as_of=now,
        decision_at=now,
        created_at=now,
    )
    with pytest.raises(Exception):
        trace.action = "changed"
