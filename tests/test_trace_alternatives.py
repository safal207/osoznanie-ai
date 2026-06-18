from datetime import UTC, datetime

from osoznanie.decision_trace import DecisionTrace, TraceAuthorizationDecision


def test_alternatives_are_normalized() -> None:
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    trace = DecisionTrace(
        id="dtr_alternatives",
        requester_id="reader",
        agent_id="agent",
        action="report.generate",
        authorization_decision=TraceAuthorizationDecision.ALLOW,
        policy_memory_ids=["mem_policy"],
        as_of=now,
        decision_at=now,
        alternatives_considered=["wait", "skip", "wait"],
        created_at=now,
    )
    assert trace.alternatives_considered == ["skip", "wait"]
