from datetime import UTC, datetime

from osoznanie.action_outbox import ActionIntentStatus, build_action_intent
from osoznanie.decision_trace import DecisionTrace, TraceAuthorizationDecision
from osoznanie.orchestration import DecisionProposal


def test_action_intent_is_deterministic_and_private() -> None:
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    trace = DecisionTrace(
        id="dtr_model",
        requester_id="reader",
        agent_id="agent",
        action="report.generate",
        authorization_decision=TraceAuthorizationDecision.ALLOW,
        policy_memory_ids=["mem_policy"],
        memory_ids=["mem_state"],
        as_of=now,
        known_at=now,
        decision_at=now,
        tool_name="report.tool",
        tool_call_id="call_1",
        input_hash="sha256:input",
        created_at=now,
    )
    proposal = DecisionProposal(
        action="report.generate",
        tool_name="report.tool",
        tool_call_id="call_1",
        input_hash="sha256:input",
    )
    left = build_action_intent(trace, proposal)
    right = build_action_intent(trace, proposal)
    assert left == right
    assert left.status is ActionIntentStatus.PENDING
    assert left.idempotency_key.startswith("osi_")
    serialized = left.model_dump_json()
    assert "mem_policy" not in serialized
    assert "mem_state" not in serialized
