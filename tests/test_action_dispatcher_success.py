from datetime import timedelta

from pydantic import BaseModel, Field

from osoznanie.action_attempt_store import SQLiteActionAttemptStore
from osoznanie.action_dispatcher import (
    ActionWorkerDispatcher,
    DispatcherStatus,
    ResolvedToolInput,
    ToolExecutionContext,
    ToolExecutionResult,
)
from osoznanie.action_outbox import ActionIntentStatus

from .action_outbox_fixtures import (
    T0,
    make_outbox,
    make_proposal,
    prepare_trace,
    save_outcome,
)


class ReportInput(BaseModel):
    report_id: str = Field(min_length=1)


class SequenceClock:
    def __init__(self, *values):
        self.values = list(values)

    def __call__(self):
        return self.values.pop(0)


class StaticResolver:
    def __init__(self, payload, input_hash="sha256:input"):
        self.payload = payload
        self.input_hash = input_hash
        self.calls = 0

    def resolve(self, intent):
        self.calls += 1
        return ResolvedToolInput(
            payload=self.payload,
            input_hash=self.input_hash,
        )


class SuccessfulReportAdapter:
    tool_name = "report.tool"
    input_model = ReportInput

    def __init__(self, store, outcome_id):
        self.store = store
        self.outcome_id = outcome_id
        self.request = None
        self.context = None
        self.started_was_persisted = False

    def execute(
        self,
        request: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        self.request = request
        self.context = context
        attempts = SQLiteActionAttemptStore(self.store).list(context.intent_id)
        self.started_was_persisted = len(attempts) == 1
        return ToolExecutionResult.succeeded(
            self.outcome_id,
            response_hash="sha256:response",
        )


def test_dispatcher_persists_started_before_adapter_and_completes() -> None:
    store, _, outbox = make_outbox()
    trace = prepare_trace(store)
    intent = outbox.enqueue(trace, make_proposal())
    outcome = save_outcome(
        store,
        observed_at=T0 + timedelta(minutes=3),
    )
    resolver = StaticResolver({"report_id": "weekly"})
    adapter = SuccessfulReportAdapter(store, outcome.id)
    dispatcher = ActionWorkerDispatcher(
        store,
        "worker-one",
        resolver,
        [adapter],
        lease_for=timedelta(minutes=10),
        clock=SequenceClock(
            T0 + timedelta(minutes=1),
            T0 + timedelta(minutes=3),
        ),
        outbox=outbox,
    )

    result = dispatcher.dispatch_once()

    persisted = outbox.get(intent.id)
    attempts = SQLiteActionAttemptStore(store, outbox).list(intent.id)
    assert result.status is DispatcherStatus.COMPLETED
    assert result.outcome_id == outcome.id
    assert persisted.status is ActionIntentStatus.COMPLETED
    assert persisted.outcome_id == outcome.id
    assert adapter.started_was_persisted is True
    assert isinstance(adapter.request, ReportInput)
    assert adapter.request.report_id == "weekly"
    assert adapter.context is not None
    assert adapter.context.intent_id == intent.id
    assert adapter.context.idempotency_key == intent.idempotency_key
    assert len(attempts) == 2
    assert attempts[0].id == result.started_attempt_id
    assert attempts[1].id == result.terminal_attempt_id


def test_dispatcher_returns_no_work_without_resolving_input() -> None:
    store, _, outbox = make_outbox()
    resolver = StaticResolver({"report_id": "weekly"})
    dispatcher = ActionWorkerDispatcher(
        store,
        "worker-one",
        resolver,
        [],
        clock=SequenceClock(T0),
        outbox=outbox,
    )

    result = dispatcher.dispatch_once()

    assert result.status is DispatcherStatus.NO_WORK
    assert result.intent_id is None
    assert resolver.calls == 0
