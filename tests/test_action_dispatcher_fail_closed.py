from datetime import timedelta

from pydantic import BaseModel, Field

from osoznanie.action_attempt_store import SQLiteActionAttemptStore
from osoznanie.action_dispatcher import (
    ActionWorkerDispatcher,
    DispatcherStatus,
    ResolvedToolInput,
    ToolExecutionResult,
)
from osoznanie.action_outbox import ActionIntentStatus

from .action_outbox_fixtures import T0, make_outbox, make_proposal, prepare_trace


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


class CountingAdapter:
    tool_name = "report.tool"
    input_model = ReportInput

    def __init__(self):
        self.calls = 0

    def execute(self, request: BaseModel, context):
        del request, context
        self.calls += 1
        return ToolExecutionResult.permanent("should_not_execute")


def make_dispatcher(store, outbox, resolver, adapters):
    return ActionWorkerDispatcher(
        store,
        "worker-one",
        resolver,
        adapters,
        lease_for=timedelta(minutes=10),
        clock=SequenceClock(
            T0 + timedelta(minutes=1),
            T0 + timedelta(minutes=2),
        ),
        outbox=outbox,
    )


def test_unknown_tool_fails_without_resolving_payload() -> None:
    store, _, outbox = make_outbox()
    trace = prepare_trace(store, tool_name="missing.tool")
    intent = outbox.enqueue(
        trace,
        make_proposal(tool_name="missing.tool"),
    )
    resolver = StaticResolver({"report_id": "weekly"})
    dispatcher = make_dispatcher(store, outbox, resolver, [])

    result = dispatcher.dispatch_once()

    persisted = outbox.get(intent.id)
    attempts = SQLiteActionAttemptStore(store, outbox).list(intent.id)
    assert result.status is DispatcherStatus.FAILED
    assert result.error_code == "unknown_tool"
    assert persisted.status is ActionIntentStatus.FAILED
    assert persisted.last_error_code == "unknown_tool"
    assert resolver.calls == 0
    assert len(attempts) == 2


def test_input_hash_mismatch_fails_before_adapter_execution() -> None:
    store, _, outbox = make_outbox()
    trace = prepare_trace(store)
    intent = outbox.enqueue(trace, make_proposal())
    resolver = StaticResolver(
        {"report_id": "weekly"},
        input_hash="sha256:different",
    )
    adapter = CountingAdapter()
    dispatcher = make_dispatcher(store, outbox, resolver, [adapter])

    result = dispatcher.dispatch_once()

    persisted = outbox.get(intent.id)
    assert result.status is DispatcherStatus.FAILED
    assert result.error_code == "input_hash_mismatch"
    assert persisted.status is ActionIntentStatus.FAILED
    assert adapter.calls == 0


def test_invalid_typed_input_fails_before_adapter_execution() -> None:
    store, _, outbox = make_outbox()
    trace = prepare_trace(store)
    intent = outbox.enqueue(trace, make_proposal())
    resolver = StaticResolver({})
    adapter = CountingAdapter()
    dispatcher = make_dispatcher(store, outbox, resolver, [adapter])

    result = dispatcher.dispatch_once()

    persisted = outbox.get(intent.id)
    assert result.status is DispatcherStatus.FAILED
    assert result.error_code == "input_validation_failed"
    assert persisted.status is ActionIntentStatus.FAILED
    assert adapter.calls == 0
