from datetime import UTC, datetime, timedelta

import pytest

from osoznanie.decision_trace import DecisionTrace, TraceAuthorizationDecision


def test_decision_cannot_precede_effective_time() -> None:
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="earlier than as_of"):
        DecisionTrace(
            id="dtr_before_effective",
            requester_id="reader",
            agent_id="agent",
            action="report.generate",
            authorization_decision=TraceAuthorizationDecision.ALLOW,
            policy_memory_ids=["mem_policy"],
            as_of=now,
            known_at=now - timedelta(seconds=1),
            decision_at=now - timedelta(seconds=1),
            created_at=now,
        )
