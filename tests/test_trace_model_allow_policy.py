from datetime import UTC, datetime

import pytest

from osoznanie.decision_trace import DecisionTrace, TraceAuthorizationDecision


def test_allow_trace_requires_policy_provenance() -> None:
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="policy memory id"):
        DecisionTrace(
            id="dtr_no_policy",
            requester_id="reader",
            agent_id="agent",
            action="report.generate",
            authorization_decision=TraceAuthorizationDecision.ALLOW,
            as_of=now,
            known_at=now,
            decision_at=now,
            created_at=now,
        )
