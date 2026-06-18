from datetime import UTC, datetime, timedelta

import pytest

from osoznanie.access_control import (
    AccessDecisionTrace,
    AccessEffect,
    AccessReasonCode,
    AccessResource,
    AccessResourceKind,
    AuthorizationDecision,
    AuthorizationQuery,
    AuthorizationResult,
    AuthorizedRule,
    AuthorizedScope,
)
from osoznanie.memory import MemoryObject, MemoryType
from osoznanie.memory_view import CommittedMemoryVersion
from osoznanie.models import Outcome, OutcomeStatus
from osoznanie.orchestration import (
    ActionExecutionError,
    AuditedDecisionOrchestrator,
    AuditedDecisionRequest,
    AuditedDecisionStatus,
    DecisionProposal,
    DecisionProposalError,
    OutcomePersistenceError,
    TracePersistenceError,
)

T0 = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)


class FakeAuthorization:
    def __init__(self, result: AuthorizationResult) -> None:
        self.result = result
        self.calls = 0

    def authorize(self, query: AuthorizationQuery) -> AuthorizationResult:
        self.calls += 1
        return self.result


class FakeMemoryStore:
    def __init__(self, history: list[CommittedMemoryVersion]) -> None:
        self.history = history
        self.calls = 0

    def list_authorized_memory_versions(
        self,
        scope: AuthorizedScope,
    ) -> list[CommittedMemoryVersion]:
        self.calls += 1
        return [
            item
            for item in self.history
            if scope.allows(item.memory.memory_key, item.memory.memory_type)
        ]


class FakeTraceStore:
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail
        self.records = {}

    def exists(self, trace_id: str) -> bool:
        return trace_id in self.records

    def save(self, trace):
        self.events.append(f"trace:{trace.trace_version}")
        if self.fail:
            raise RuntimeError("trace store unavailable")
        self.records[trace.id] = trace
        return trace


class FakeOutcomeStore:
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail
        self.records = {}

    def save(self, outcome: Outcome) -> Outcome:
        self.events.append("outcome")
        if self.fail:
            raise RuntimeError("outcome store unavailable")
        self.records[outcome.id] = outcome
        return outcome


def query() -> AuthorizationQuery:
    return AuthorizationQuery(
        requester_id="agent_reader",
        action="report.generate",
        as_of=T0,
        known_at=T0,
        memory_keys=["profile.private"],
    )


def request(*, require_memory_context: bool = True) -> AuditedDecisionRequest:
    return AuditedDecisionRequest(
        authorization_query=query(),
        agent_id="agent_executor",
        decision_at=T0 + timedelta(minutes=5),
        require_memory_context=require_memory_context,
    )


def allow_result() -> AuthorizationResult:
    rule = AuthorizedRule(
        policy_memory_id="mem_policy_allow",
        resource=AccessResource(
            kind=AccessResourceKind.EXACT_KEY,
            value="profile.private",
        ),
        effect=AccessEffect.ALLOW,
    )
    scope = AuthorizedScope(
        requested_memory_keys=["profile.private"],
        requested_key_prefixes=[],
        requested_memory_types=[],
        rules=[rule],
    )
    trace = AccessDecisionTrace(
        requester_id="agent_reader",
        action="report.generate",
        as_of=T0,
        known_at=T0,
        decision=AuthorizationDecision.ALLOW,
        reason_codes=[AccessReasonCode.POLICY_ALLOWED],
        matched_policy_memory_ids=["mem_policy_allow"],
        requested_memory_keys=["profile.private"],
        requested_key_prefixes=[],
        requested_memory_types=[],
    )
    return AuthorizationResult(scope=scope, trace=trace)


def deny_result() -> AuthorizationResult:
    scope = AuthorizedScope(
        requested_memory_keys=["profile.private"],
        requested_key_prefixes=[],
        requested_memory_types=[],
        rules=[],
    )
    trace = AccessDecisionTrace(
        requester_id="agent_reader",
        action="report.generate",
        as_of=T0,
        known_at=T0,
        decision=AuthorizationDecision.DENY,
        reason_codes=[AccessReasonCode.DEFAULT_DENY],
        matched_policy_memory_ids=[],
        requested_memory_keys=["profile.private"],
        requested_key_prefixes=[],
        requested_memory_types=[],
    )
    return AuthorizationResult(scope=scope, trace=trace)


def committed_memory() -> CommittedMemoryVersion:
    memory = MemoryObject(
        id="mem_profile_private",
        memory_key="profile.private",
        memory_type=MemoryType.FACT,
        content={"state": "verified"},
        source_event_ids=["evt_profile"],
        confidence=1.0,
        importance=1.0,
        valid_from=T0,
        created_at=T0,
        updated_at=T0,
    )
    return CommittedMemoryVersion(memory=memory, committed_at=T0)


def proposal(action: str = "report.generate") -> DecisionProposal:
    return DecisionProposal(
        action=action,
        alternatives_considered=["defer"],
        reason_codes=["context_verified"],
        tool_name="report.tool",
        tool_call_id="call_1",
        input_hash="sha256:input",
    )


def orchestrator(
    auth_result: AuthorizationResult,
    events: list[str],
    *,
    history: list[CommittedMemoryVersion] | None = None,
    trace_fail: bool = False,
    outcome_fail: bool = False,
):
    authorization = FakeAuthorization(auth_result)
    memory_store = FakeMemoryStore(history or [])
    trace_store = FakeTraceStore(events, fail=trace_fail)
    outcome_store = FakeOutcomeStore(events, fail=outcome_fail)
    engine = AuditedDecisionOrchestrator(
        authorization=authorization,
        memory_store=memory_store,
        trace_store=trace_store,
        outcome_store=outcome_store,
    )
    return engine, authorization, memory_store, trace_store, outcome_store


def test_denied_request_never_calls_decider_or_executor() -> None:
    events = []
    engine, authorization, memory_store, trace_store, _ = orchestrator(
        deny_result(),
        events,
        history=[committed_memory()],
    )

    def decide(_):
        events.append("decide")
        return proposal()

    def execute(_, __):
        events.append("execute")
        return None

    result = engine.run(request(), decide, execute)

    assert result.status is AuditedDecisionStatus.DENIED
    assert result.memory_view.entries == []
    assert result.initial_trace_id is None
    assert events == []
    assert authorization.calls == 1
    assert memory_store.calls == 0
    assert trace_store.records == {}
    assert "policy" not in result.model_dump_json()


def test_trace_is_persisted_before_action_execution() -> None:
    events = []
    engine, authorization, _, trace_store, _ = orchestrator(
        allow_result(),
        events,
        history=[committed_memory()],
    )

    def decide(context):
        events.append("decide")
        assert [entry.memory.id for entry in context.memory_view.entries] == [
            "mem_profile_private"
        ]
        assert not hasattr(context, "authorization")
        return proposal()

    def execute(_, trace):
        events.append("execute")
        assert trace.id in trace_store.records
        return None

    result = engine.run(request(), decide, execute)

    assert result.status is AuditedDecisionStatus.ACTION_COMPLETED
    assert events == ["decide", "trace:1", "execute"]
    assert authorization.calls == 1
    trace = trace_store.records[result.initial_trace_id]
    assert trace.policy_memory_ids == ["mem_policy_allow"]
    assert trace.memory_ids == ["mem_profile_private"]


def test_trace_failure_prevents_action_execution() -> None:
    events = []
    engine, _, _, _, _ = orchestrator(
        allow_result(),
        events,
        history=[committed_memory()],
        trace_fail=True,
    )

    def execute(_, __):
        events.append("execute")
        return None

    with pytest.raises(TracePersistenceError):
        engine.run(request(), lambda _: proposal(), execute)

    assert events == ["trace:1"]


def test_proposal_action_must_match_authorized_action() -> None:
    events = []
    engine, _, _, trace_store, _ = orchestrator(
        allow_result(),
        events,
        history=[committed_memory()],
    )

    with pytest.raises(DecisionProposalError, match="match"):
        engine.run(request(), lambda _: proposal("email.send"))

    assert trace_store.records == {}


def test_action_failure_preserves_initial_trace() -> None:
    events = []
    engine, _, _, trace_store, _ = orchestrator(
        allow_result(),
        events,
        history=[committed_memory()],
    )

    def execute(_, __):
        events.append("execute")
        raise RuntimeError("tool failed")

    with pytest.raises(ActionExecutionError) as raised:
        engine.run(request(), lambda _: proposal(), execute)

    assert raised.value.trace_id in trace_store.records
    assert events == ["trace:1", "execute"]


def test_synchronous_outcome_creates_superseding_trace() -> None:
    events = []
    engine, _, _, trace_store, outcome_store = orchestrator(
        allow_result(),
        events,
        history=[committed_memory()],
    )
    outcome = Outcome(
        id="out_report",
        decision_id="dec_report",
        status=OutcomeStatus.SUCCESS,
        summary="Report generated.",
        observed_at=T0 + timedelta(minutes=10),
        created_at=T0 + timedelta(minutes=10),
    )

    result = engine.run(
        request(),
        lambda _: proposal(),
        lambda _, __: outcome,
    )

    assert result.status is AuditedDecisionStatus.OUTCOME_TRACED
    assert events == ["trace:1", "outcome", "trace:2"]
    assert result.outcome_id in outcome_store.records
    completed = trace_store.records[result.outcome_trace_id]
    assert completed.supersedes_trace_id == result.initial_trace_id
    assert completed.outcome_id == outcome.id


def test_retry_of_same_trace_does_not_repeat_action() -> None:
    events = []
    engine, _, _, _, _ = orchestrator(
        allow_result(),
        events,
        history=[committed_memory()],
    )

    def execute(_, __):
        events.append("execute")
        return None

    first = engine.run(request(), lambda _: proposal(), execute)
    second = engine.run(request(), lambda _: proposal(), execute)

    assert first.status is AuditedDecisionStatus.ACTION_COMPLETED
    assert second.status is AuditedDecisionStatus.ALREADY_TRACED
    assert events == ["trace:1", "execute", "trace:1"]


def test_empty_authorized_context_fails_closed_by_default() -> None:
    events = []
    engine, _, _, trace_store, _ = orchestrator(allow_result(), events)

    def decide(_):
        events.append("decide")
        return proposal()

    result = engine.run(request(), decide)

    assert result.status is AuditedDecisionStatus.NO_AUTHORIZED_CONTEXT
    assert events == []
    assert trace_store.records == {}


def test_outcome_persistence_failure_preserves_initial_trace() -> None:
    events = []
    engine, _, _, trace_store, _ = orchestrator(
        allow_result(),
        events,
        history=[committed_memory()],
        outcome_fail=True,
    )
    outcome = Outcome(
        id="out_failed_save",
        decision_id="dec_report",
        status=OutcomeStatus.SUCCESS,
        summary="Report generated.",
        observed_at=T0 + timedelta(minutes=10),
        created_at=T0 + timedelta(minutes=10),
    )

    with pytest.raises(OutcomePersistenceError) as raised:
        engine.run(request(), lambda _: proposal(), lambda _, __: outcome)

    assert raised.value.trace_id in trace_store.records
    assert events == ["trace:1", "outcome"]
