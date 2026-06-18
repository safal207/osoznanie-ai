from datetime import UTC, datetime

from osoznanie.decision_trace import DecisionTrace, TraceAuthorizationDecision


def test_canonical_json_is_stable() -> None:
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    trace = DecisionTrace(
        id="dtr_json",
        requester_id="reader",
        agent_id="agent",
        action="report.generate",
        authorization_decision=TraceAuthorizationDecision.ALLOW,
        policy_memory_ids=["mem_policy"],
        as_of=now,
        known_at=now,
        decision_at=now,
        created_at=now,
    )
    assert trace.canonical_json() == trace.canonical_json()
