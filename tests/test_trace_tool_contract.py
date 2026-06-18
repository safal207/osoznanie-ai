from datetime import UTC, datetime

import pytest

from osoznanie.decision_trace import DecisionTrace, TraceAuthorizationDecision


def test_tool_call_id_requires_tool_name() -> None:
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="tool_name"):
        DecisionTrace(
            id="dtr_tool",
            requester_id="reader",
            agent_id="agent",
            action="report.generate",
            authorization_decision=TraceAuthorizationDecision.ALLOW,
            policy_memory_ids=["mem_policy"],
            as_of=now,
            known_at=now,
            decision_at=now,
            tool_call_id="call_1",
            created_at=now,
        )
