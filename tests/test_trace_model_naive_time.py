from datetime import datetime

import pytest

from osoznanie.decision_trace import DecisionTrace, TraceAuthorizationDecision


def test_trace_rejects_naive_timestamps() -> None:
    naive = datetime(2026, 6, 18, 12, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        DecisionTrace(
            id="dtr_naive",
            requester_id="reader",
            agent_id="agent",
            action="report.generate",
            authorization_decision=TraceAuthorizationDecision.ALLOW,
            policy_memory_ids=["mem_policy"],
            as_of=naive,
            known_at=naive,
            decision_at=naive,
            created_at=naive,
        )
