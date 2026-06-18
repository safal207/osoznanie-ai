from datetime import UTC, datetime

import pytest

from osoznanie.decision_trace import DecisionTrace, TraceAuthorizationDecision


def test_later_trace_version_requires_predecessor() -> None:
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="supersede"):
        DecisionTrace(
            id="dtr_v2",
            requester_id="reader",
            agent_id="agent",
            action="report.generate",
            authorization_decision=TraceAuthorizationDecision.ALLOW,
            policy_memory_ids=["mem_policy"],
            as_of=now,
            known_at=now,
            decision_at=now,
            trace_version=2,
            created_at=now,
        )
