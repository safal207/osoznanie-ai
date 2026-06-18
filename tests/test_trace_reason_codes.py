from datetime import UTC, datetime

from osoznanie.decision_trace import DecisionTrace, TraceAuthorizationDecision


def test_reason_codes_are_sorted_and_deduplicated() -> None:
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    trace = DecisionTrace(
        id="dtr_reasons",
        requester_id="reader",
        agent_id="agent",
        action="report.generate",
        authorization_decision=TraceAuthorizationDecision.ALLOW,
        policy_memory_ids=["mem_policy"],
        as_of=now,
        decision_at=now,
        reason_codes=["z", "a", "z"],
        created_at=now,
    )
    assert trace.reason_codes == ["a", "z"]
