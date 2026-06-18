from datetime import UTC, datetime

from osoznanie.decision_trace import DecisionTrace, TraceAuthorizationDecision


def test_trace_id_payload_is_order_stable_after_normalization() -> None:
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    left = DecisionTrace(
        id="pending",
        requester_id="reader",
        agent_id="agent",
        action="report.generate",
        authorization_decision=TraceAuthorizationDecision.ALLOW,
        policy_memory_ids=["mem_b", "mem_a"],
        memory_ids=["mem_2", "mem_1"],
        as_of=now,
        known_at=now,
        decision_at=now,
        created_at=now,
    )
    right = left.model_copy(update={"policy_memory_ids": ["mem_a", "mem_b"], "memory_ids": ["mem_1", "mem_2"]})
    assert DecisionTrace.derive_id(left.canonical_payload()) == DecisionTrace.derive_id(right.canonical_payload())
