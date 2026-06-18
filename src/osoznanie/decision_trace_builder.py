"""Build deterministic decision traces from captured authorization and memory views."""

from __future__ import annotations

from datetime import UTC, datetime

from .access_control import (
    AccessDecisionTrace,
    AuthorizationDecision,
)
from .decision_trace import DecisionTrace, TraceAuthorizationDecision
from .memory_view import MemoryView
from .models import Outcome


class DecisionTraceBuildError(ValueError):
    """Base exception for invalid decision-trace construction."""


class ActionNotAuthorizedError(DecisionTraceBuildError):
    """Raised when a denied authorization is used to construct an action trace."""


class DecisionContextMismatchError(DecisionTraceBuildError):
    """Raised when authorization and memory snapshots use different timelines."""


class OutcomeAlreadyAttachedError(DecisionTraceBuildError):
    """Raised when a trace is linked to a different outcome already."""


def _aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


class DecisionTraceBuilder:
    """Capture exact ids from supplied snapshots without resolving current heads."""

    def build(
        self,
        *,
        access_trace: AccessDecisionTrace,
        memory_view: MemoryView,
        agent_id: str,
        action: str,
        decision_at: datetime,
        alternatives_considered: list[str] | None = None,
        reason_codes: list[str] | None = None,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        input_hash: str | None = None,
        outcome_id: str | None = None,
    ) -> DecisionTrace:
        if access_trace.decision is not AuthorizationDecision.ALLOW:
            raise ActionNotAuthorizedError(
                "an action-producing decision trace requires allow authorization"
            )
        if (
            memory_view.as_of != access_trace.as_of
            or memory_view.known_at != access_trace.known_at
        ):
            raise DecisionContextMismatchError(
                "authorization trace and memory view must use identical as_of/known_at"
            )

        captured_reason_codes = [
            *(code.value for code in access_trace.reason_codes),
            *(reason_codes or []),
        ]
        normalized_decision_at = _aware_utc(
            decision_at,
            field_name="decision_at",
        )
        return self._create(
            requester_id=access_trace.requester_id,
            agent_id=agent_id,
            action=action,
            authorization_decision=TraceAuthorizationDecision.ALLOW,
            policy_memory_ids=access_trace.matched_policy_memory_ids,
            memory_ids=[entry.memory.id for entry in memory_view.entries],
            as_of=access_trace.as_of,
            known_at=access_trace.known_at,
            decision_at=normalized_decision_at,
            alternatives_considered=alternatives_considered or [],
            reason_codes=captured_reason_codes,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            input_hash=input_hash,
            outcome_id=outcome_id,
            supersedes_trace_id=None,
            trace_version=1,
            created_at=normalized_decision_at,
        )

    def attach_outcome(
        self,
        trace: DecisionTrace,
        outcome: Outcome,
    ) -> DecisionTrace:
        """Return a new immutable trace version linked to a later outcome."""
        if trace.outcome_id == outcome.id:
            return trace
        if trace.outcome_id is not None:
            raise OutcomeAlreadyAttachedError(
                f"trace already references a different outcome: {trace.outcome_id}"
            )
        return self._create(
            requester_id=trace.requester_id,
            agent_id=trace.agent_id,
            action=trace.action,
            authorization_decision=trace.authorization_decision,
            policy_memory_ids=trace.policy_memory_ids,
            memory_ids=trace.memory_ids,
            as_of=trace.as_of,
            known_at=trace.known_at,
            decision_at=trace.decision_at,
            alternatives_considered=trace.alternatives_considered,
            reason_codes=trace.reason_codes,
            tool_name=trace.tool_name,
            tool_call_id=trace.tool_call_id,
            input_hash=trace.input_hash,
            outcome_id=outcome.id,
            supersedes_trace_id=trace.id,
            trace_version=trace.trace_version + 1,
            created_at=_aware_utc(
                outcome.observed_at,
                field_name="outcome.observed_at",
            ),
        )

    @staticmethod
    def _create(**values: object) -> DecisionTrace:
        provisional = DecisionTrace(id="dtr_pending", **values)
        trace_id = DecisionTrace.derive_id(provisional.canonical_payload())
        return provisional.model_copy(update={"id": trace_id})
