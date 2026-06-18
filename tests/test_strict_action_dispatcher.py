from datetime import timedelta

from pydantic import BaseModel, Field

from osoznanie.action_attempt_store import SQLiteActionAttemptStore
from osoznanie.action_dispatcher import (
    DispatcherStatus,
    ResolvedToolInput,
    ToolExecutionResult,
)
from osoznanie.action_outbox import ActionIntentStatus
from osoznanie.strict_action_dispatcher import StrictActionWorkerDispatcher

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
    def resolve(self, intent):
        del intent
        return ResolvedToolInput(
            payload={"report_id": "weekly"},
            input_hash="sha256:input",
        )


class MalformedAdapter:
    tool_name = "report.tool"
    input_model = ReportInput

    def execute(self, request, context):
        del request, context
        return {"kind": "succeeded", "secret": "must-not-persist"}


class ValidAdapter:
    tool_name = "report.tool"
    input_model = ReportInput

    def __init__(self, outcome_id: str):
        self.outcome_id = outcome_id

    def execute(self, request, context):
        del request, context
        return ToolExecutionResult.succeeded(self.outcome_id)


def make_dispatcher(store, outbox, adapter):
    return StrictActionWorkerDispatcher(
        store,
        "worker-one",
        StaticResolver(),
        [adapter],
        lease_for=timedelta(minutes=10),
        clock=SequenceClock(
            T0 + timedelta(minutes=1),
            T0 + timedelta(minutes=2),
        ),
        outbox=outbox,
    )


def test_malformed_adapter_result_fails_closed_and_releases_lease() -> None:
    store, _, outbox = make_outbox()
    trace = prepare_trace(store)
    intent = outbox.enqueue(trace, make_proposal())
    dispatcher = make_dispatcher(store, outbox, MalformedAdapter())

    result = dispatcher.dispatch_once()

    persisted = outbox.get(intent.id)
    attempts = SQLiteActionAttemptStore(store, outbox).list(intent.id)
    assert result.status is DispatcherStatus.FAILED
    assert result.error_code == "invalid_adapter_result"
    assert persisted.status is ActionIntentStatus.FAILED
    assert persisted.last_error_code == "invalid_adapter_result"
    assert persisted.lease_token is None
    assert len(attempts) == 2
    assert attempts[-1].error_code == "invalid_adapter_result"
    assert "must-not-persist" not in attempts[-1].model_dump_json()


def test_strict_dispatcher_preserves_valid_success_result() -> None:
    store, _, outbox = make_outbox()
    trace = prepare_trace(store)
    intent = outbox.enqueue(trace, make_proposal())
    outcome = save_outcome(
        store,
        observed_at=T0 + timedelta(minutes=2),
    )
    dispatcher = make_dispatcher(store, outbox, ValidAdapter(outcome.id))

    result = dispatcher.dispatch_once()

    assert result.status is DispatcherStatus.COMPLETED
    assert result.outcome_id == outcome.id
    assert outbox.get(intent.id).status is ActionIntentStatus.COMPLETED
