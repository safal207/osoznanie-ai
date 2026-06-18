from datetime import UTC, datetime, timedelta

import pytest

from osoznanie.access_control import (
    AccessDecisionTrace,
    AccessReasonCode,
    AuthorizationDecision,
)
from osoznanie.decision_trace_builder import (
    DecisionContextMismatchError,
    DecisionTraceBuilder,
)
from osoznanie.memory_view import MemoryView, MemoryViewFilterCounts


def test_builder_rejects_mismatched_timelines() -> None:
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    access = AccessDecisionTrace(
        requester_id="reader",
        action="observe",
        as_of=now,
        known_at=now,
        decision=AuthorizationDecision.ALLOW,
        reason_codes=[AccessReasonCode.POLICY_ALLOWED],
        matched_policy_memory_ids=["mem_policy"],
        requested_memory_keys=[],
        requested_key_prefixes=[],
        requested_memory_types=[],
    )
    view = MemoryView(
        as_of=now + timedelta(seconds=1),
        known_at=now,
        entries=[],
        rejections=[],
        filter_counts=MemoryViewFilterCounts(),
    )
    with pytest.raises(DecisionContextMismatchError):
        DecisionTraceBuilder().build(
            access_trace=access,
            memory_view=view,
            agent_id="agent",
            action="report.generate",
            decision_at=now + timedelta(seconds=2),
        )
