from datetime import UTC, datetime

from osoznanie.decision_trace import DecisionTrace, TraceAuthorizationDecision


def test_trace_reference_ids_include_context() -> None:
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    trace = DecisionTrace(
        id="dtr_refs",
        requester_id="reader",
        agent_id="agent",
        action="report.generate",
        authorization_decision=TraceAuthorizationDecision.ALLOW,
        policy_memory_ids=["mem_policy"],
        memory_ids=["mem_state"],
        as_of=now,
        known_at=now,
        decision_at=now,
        created_at=now,
    )
    assert trace.reference_ids() == ("mem_policy", "mem_state")
