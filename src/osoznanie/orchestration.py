"""Mandatory authorization, projection, trace, and action orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .access_control import (
    AuthorizationDecision,
    AuthorizationEngine,
    AuthorizationQuery,
    AuthorizedMemoryStore,
    AuthorizedScope,
)
from .decision_trace import DecisionTrace
from .decision_trace_builder import DecisionTraceBuilder
from .memory_view import (
    CommittedMemoryVersion,
    MemoryView,
    MemoryViewEngine,
    MemoryViewFilterCounts,
    MemoryViewQuery,
)
from .models import Outcome


class OrchestrationError(RuntimeError):
    """Base exception for the mandatory audited-decision pipeline."""


class DecisionProposalError(OrchestrationError):
    """The decision callback returned an invalid or unauthorized proposal."""


class TracePersistenceError(OrchestrationError):
    """The initial immutable trace could not be durably persisted."""

    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id
        super().__init__(f"failed to persist initial decision trace: {trace_id}")


class ActionExecutionError(OrchestrationError):
    """The action failed after its initial trace was persisted."""

    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id
        super().__init__(f"action failed after trace persistence: {trace_id}")


class OutcomePersistenceError(OrchestrationError):
    """An action outcome or its superseding trace could not be persisted."""

    def __init__(self, trace_id: str, outcome_id: str | None = None) -> None:
        self.trace_id = trace_id
        self.outcome_id = outcome_id
        super().__init__(
            f"failed to persist outcome evidence for trace {trace_id}: {outcome_id}"
        )


class AuditedDecisionStatus(StrEnum):
    DENIED = "denied"
    NO_AUTHORIZED_CONTEXT = "no_authorized_context"
    TRACE_PERSISTED = "trace_persisted"
    ALREADY_TRACED = "already_traced"
    ACTION_COMPLETED = "action_completed"
    OUTCOME_TRACED = "outcome_traced"


def _normalize_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("value must not be blank")
    return normalized


def _normalize_texts(values: list[str]) -> list[str]:
    return sorted({value.strip() for value in values if value.strip()})


def _aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


class DecisionProposal(BaseModel):
    """Action metadata returned by a decision callback, without private reasoning."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str = Field(min_length=1)
    alternatives_considered: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    tool_name: str | None = None
    tool_call_id: str | None = None
    input_hash: str | None = None

    @field_validator("action")
    @classmethod
    def normalize_action(cls, value: str) -> str:
        return _normalize_text(value)

    @field_validator("alternatives_considered", "reason_codes")
    @classmethod
    def normalize_lists(cls, values: list[str]) -> list[str]:
        return _normalize_texts(values)

    @field_validator("tool_name", "tool_call_id", "input_hash")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_tool_metadata(self) -> DecisionProposal:
        if self.tool_call_id is not None and self.tool_name is None:
            raise ValueError("tool_call_id requires tool_name")
        return self


class DecisionContext(BaseModel):
    """The only context exposed to the decision callback."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requester_id: str
    authorized_action: str
    as_of: datetime
    known_at: datetime | None
    memory_view: MemoryView


class AuditedDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authorization_query: AuthorizationQuery
    agent_id: str = Field(min_length=1)
    decision_at: datetime
    require_memory_context: bool = True

    @field_validator("agent_id")
    @classmethod
    def normalize_agent_id(cls, value: str) -> str:
        return _normalize_text(value)

    @field_validator("decision_at")
    @classmethod
    def normalize_decision_at(cls, value: datetime) -> datetime:
        return _aware_utc(value, field_name="decision_at")

    @model_validator(mode="after")
    def validate_timeline(self) -> AuditedDecisionRequest:
        if self.decision_at < self.authorization_query.as_of:
            raise ValueError("decision_at must not be earlier than as_of")
        if (
            self.authorization_query.known_at is not None
            and self.authorization_query.known_at > self.decision_at
        ):
            raise ValueError("known_at must not be later than decision_at")
        return self


class AuditedDecisionResult(BaseModel):
    """External non-disclosing result of the audited pipeline."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    authorization_decision: AuthorizationDecision
    status: AuditedDecisionStatus
    memory_view: MemoryView
    initial_trace_id: str | None = None
    outcome_trace_id: str | None = None
    outcome_id: str | None = None


class DecisionCallback(Protocol):
    def __call__(self, context: DecisionContext) -> DecisionProposal: ...


class ActionExecutor(Protocol):
    def __call__(
        self,
        proposal: DecisionProposal,
        trace: DecisionTrace,
    ) -> Outcome | None: ...


class DecisionTraceSink(Protocol):
    def exists(self, trace_id: str) -> bool: ...

    def save(self, trace: DecisionTrace) -> DecisionTrace: ...


class OutcomeSink(Protocol):
    def save(self, outcome: Outcome) -> Outcome: ...


class _ScopedProjectionStore:
    def __init__(self, store: AuthorizedMemoryStore, scope: AuthorizedScope) -> None:
        self.store = store
        self.scope = scope

    def list_committed_memory_versions(self) -> list[CommittedMemoryVersion]:
        return self.store.list_authorized_memory_versions(self.scope)


class AuditedDecisionOrchestrator:
    """Enforce trace-before-action execution with one captured authorization result."""

    def __init__(
        self,
        *,
        authorization: AuthorizationEngine,
        memory_store: AuthorizedMemoryStore,
        trace_store: DecisionTraceSink,
        outcome_store: OutcomeSink | None = None,
        trace_builder: DecisionTraceBuilder | None = None,
    ) -> None:
        self.authorization = authorization
        self.memory_store = memory_store
        self.trace_store = trace_store
        self.outcome_store = outcome_store
        self.trace_builder = trace_builder or DecisionTraceBuilder()

    def run(
        self,
        request: AuditedDecisionRequest,
        decide: DecisionCallback,
        execute: ActionExecutor | None = None,
    ) -> AuditedDecisionResult:
        authorization = self.authorization.authorize(request.authorization_query)
        if authorization.trace.decision is not AuthorizationDecision.ALLOW:
            return self._result(
                request,
                AuthorizationDecision.DENY,
                AuditedDecisionStatus.DENIED,
                self._empty_view(request),
            )

        memory_view = MemoryViewEngine(
            _ScopedProjectionStore(self.memory_store, authorization.scope)
        ).project(
            MemoryViewQuery(
                as_of=request.authorization_query.as_of,
                known_at=request.authorization_query.known_at,
            )
        )
        if request.require_memory_context and not memory_view.entries:
            return self._result(
                request,
                AuthorizationDecision.ALLOW,
                AuditedDecisionStatus.NO_AUTHORIZED_CONTEXT,
                memory_view,
            )

        context = DecisionContext(
            requester_id=request.authorization_query.requester_id,
            authorized_action=request.authorization_query.action,
            as_of=request.authorization_query.as_of,
            known_at=request.authorization_query.known_at,
            memory_view=memory_view,
        )
        proposal = decide(context)
        if not isinstance(proposal, DecisionProposal):
            raise DecisionProposalError(
                "decision callback must return a DecisionProposal"
            )
        if proposal.action != request.authorization_query.action:
            raise DecisionProposalError(
                "proposed action must match the authorized action exactly"
            )

        trace = self.trace_builder.build(
            access_trace=authorization.trace,
            memory_view=memory_view,
            agent_id=request.agent_id,
            action=proposal.action,
            decision_at=request.decision_at,
            alternatives_considered=proposal.alternatives_considered,
            reason_codes=proposal.reason_codes,
            tool_name=proposal.tool_name,
            tool_call_id=proposal.tool_call_id,
            input_hash=proposal.input_hash,
        )
        already_traced = self.trace_store.exists(trace.id)
        try:
            persisted_trace = self.trace_store.save(trace)
        except Exception as error:
            raise TracePersistenceError(trace.id) from error

        if already_traced:
            return self._result(
                request,
                AuthorizationDecision.ALLOW,
                AuditedDecisionStatus.ALREADY_TRACED,
                memory_view,
                initial_trace_id=persisted_trace.id,
            )
        if execute is None:
            return self._result(
                request,
                AuthorizationDecision.ALLOW,
                AuditedDecisionStatus.TRACE_PERSISTED,
                memory_view,
                initial_trace_id=persisted_trace.id,
            )

        try:
            outcome = execute(proposal, persisted_trace)
        except Exception as error:
            raise ActionExecutionError(persisted_trace.id) from error
        if outcome is None:
            return self._result(
                request,
                AuthorizationDecision.ALLOW,
                AuditedDecisionStatus.ACTION_COMPLETED,
                memory_view,
                initial_trace_id=persisted_trace.id,
            )
        if not isinstance(outcome, Outcome):
            raise ActionExecutionError(persisted_trace.id)
        if self.outcome_store is None:
            raise OutcomePersistenceError(persisted_trace.id, outcome.id)

        try:
            persisted_outcome = self.outcome_store.save(outcome)
            outcome_trace = self.trace_builder.attach_outcome(
                persisted_trace,
                persisted_outcome,
            )
            persisted_outcome_trace = self.trace_store.save(outcome_trace)
        except Exception as error:
            raise OutcomePersistenceError(
                persisted_trace.id,
                outcome.id,
            ) from error

        return self._result(
            request,
            AuthorizationDecision.ALLOW,
            AuditedDecisionStatus.OUTCOME_TRACED,
            memory_view,
            initial_trace_id=persisted_trace.id,
            outcome_trace_id=persisted_outcome_trace.id,
            outcome_id=persisted_outcome.id,
        )

    @staticmethod
    def _empty_view(request: AuditedDecisionRequest) -> MemoryView:
        return MemoryView(
            as_of=request.authorization_query.as_of,
            known_at=request.authorization_query.known_at,
            entries=[],
            rejections=[],
            filter_counts=MemoryViewFilterCounts(),
        )

    @staticmethod
    def _result(
        request: AuditedDecisionRequest,
        decision: AuthorizationDecision,
        status: AuditedDecisionStatus,
        memory_view: MemoryView,
        *,
        initial_trace_id: str | None = None,
        outcome_trace_id: str | None = None,
        outcome_id: str | None = None,
    ) -> AuditedDecisionResult:
        del request
        return AuditedDecisionResult(
            authorization_decision=decision,
            status=status,
            memory_view=memory_view,
            initial_trace_id=initial_trace_id,
            outcome_trace_id=outcome_trace_id,
            outcome_id=outcome_id,
        )
