from datetime import UTC, datetime, timedelta

import pytest

from osoznanie.decision_trace import DecisionTrace, TraceAuthorizationDecision


def test_known_at_cannot_be_after_decision_at() -> None:
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="known_at"):
        DecisionTrace(
            id="dtr_future_knowledge",
            requester_id="reader",
            agent_id="agent",
            action="report.generate",
            authorization_decision=TraceAuthorizationDecision.ALLOW,
            policy_memory_ids=["mem_policy"],
            as_of=now,
            known_at=now + timedelta(seconds=1),
            decision_at=now,
            created_at=now,
        )
