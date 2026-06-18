from datetime import UTC, datetime

from osoznanie.decision_trace import DecisionTrace, TraceAuthorizationDecision


def test_decision_trace_normalizes_ids() -> None:
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    trace = DecisionTrace(
        id="dtr_test",
        requester_id="reader",
        agent_id="agent",
        action="report.generate",
        authorization_decision=TraceAuthorizationDecision.ALLOW,
        policy_memory_ids=["mem_b", "mem_a"],
        memory_ids=["mem_z", "mem_y"],
        as_of=now,
        known_at=now,
        decision_at=now,
        created_at=now,
    )
    assert trace.policy_memory_ids == ["mem_a", "mem_b"]
    assert trace.memory_ids == ["mem_y", "mem_z"]
