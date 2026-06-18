from datetime import UTC, datetime

import pytest

from osoznanie.access_control import (
    AccessDecisionTrace,
    AccessReasonCode,
    AuthorizationDecision,
)
from osoznanie.decision_trace_builder import (
    ActionNotAuthorizedError,
    DecisionTraceBuilder,
)
from osoznanie.memory_view import MemoryView, MemoryViewFilterCounts


def test_denied_authorization_cannot_create_action_trace() -> None:
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    access = AccessDecisionTrace(
        requester_id="reader",
        action="observe",
        as_of=now,
        known_at=now,
        decision=AuthorizationDecision.DENY,
        reason_codes=[AccessReasonCode.DEFAULT_DENY],
        matched_policy_memory_ids=[],
        requested_memory_keys=[],
        requested_key_prefixes=[],
        requested_memory_types=[],
    )
    view = MemoryView(
        as_of=now,
        known_at=now,
        entries=[],
        rejections=[],
        filter_counts=MemoryViewFilterCounts(),
    )
    with pytest.raises(ActionNotAuthorizedError):
        DecisionTraceBuilder().build(
            access_trace=access,
            memory_view=view,
            agent_id="agent",
            action="report.generate",
            decision_at=now,
        )
