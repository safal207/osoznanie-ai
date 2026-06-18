from datetime import timedelta

from pydantic import BaseModel, Field

from osoznanie.action_attempt_store import SQLiteActionAttemptStore
from osoznanie.action_dispatcher import (
    ActionWorkerDispatcher,
    DispatcherStatus,
    PermanentToolError,
    ResolvedToolInput,
    RetryableToolError,
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
    def resolve(self, intent):
        del intent
        return ResolvedToolInput(
            payload={"report_id": "weekly"},
            input_hash="sha256:input",
        )


class RetryableAdapter:
    tool_name = "report.tool"
    input_model = ReportInput

    def execute(self, request: BaseModel, context):
        del request, context
        raise RetryableToolError(
            "rate_limited",
            retry_after=timedelta(minutes=7),
        )


class PermanentAdapter:
    tool_name = "report.tool"
    input_model = ReportInput

    def execute(self, request: BaseModel, context):
        del request, context
        raise PermanentToolError("permission_denied")


class CrashingAdapter:
    tool_name = "report.tool"
    input_model = ReportInput

    def execute(self, request: BaseModel, context):
        del request, context
        raise RuntimeError("sensitive provider detail")


def prepare_dispatch(adapter, *, default_retry_after=timedelta(minutes=1)):
    store, _, outbox = make_outbox()
    trace = prepare_trace(store)
    intent = outbox.enqueue(trace, make_proposal())
    dispatcher = ActionWorkerDispatcher(
        store,
        "worker-one",
        StaticResolver(),
        [adapter],
        lease_for=timedelta(minutes=10),
        default_retry_after=default_retry_after,
        clock=SequenceClock(
            T0 + timedelta(minutes=1),
            T0 + timedelta(minutes=2),
        ),
        outbox=outbox,
    )
    return store, outbox, intent, dispatcher


def test_retryable_tool_error_schedules_requested_retry() -> None:
    store, outbox, intent, dispatcher = prepare_dispatch(RetryableAdapter())

    result = dispatcher.dispatch_once()

    persisted = outbox.get(intent.id)
    attempts = SQLiteActionAttemptStore(store, outbox).list(intent.id)
    expected_retry = T0 + timedelta(minutes=9)
    assert result.status is DispatcherStatus.RETRY_SCHEDULED
    assert result.error_code == "rate_limited"
    assert result.retry_at == expected_retry
    assert persisted.status is ActionIntentStatus.PENDING
    assert persisted.available_at == expected_retry
    assert persisted.last_error_code == "rate_limited"
    assert len(attempts) == 2


def test_permanent_tool_error_marks_intent_failed() -> None:
    store, outbox, intent, dispatcher = prepare_dispatch(PermanentAdapter())

    result = dispatcher.dispatch_once()

    persisted = outbox.get(intent.id)
    assert result.status is DispatcherStatus.FAILED
    assert result.error_code == "permission_denied"
    assert persisted.status is ActionIntentStatus.FAILED
    assert persisted.last_error_code == "permission_denied"


def test_unexpected_adapter_exception_uses_safe_default_retry() -> None:
    store, outbox, intent, dispatcher = prepare_dispatch(
        CrashingAdapter(),
        default_retry_after=timedelta(minutes=4),
    )

    result = dispatcher.dispatch_once()

    persisted = outbox.get(intent.id)
    expected_retry = T0 + timedelta(minutes=6)
    assert result.status is DispatcherStatus.RETRY_SCHEDULED
    assert result.error_code == "adapter_exception"
    assert result.retry_at == expected_retry
    assert persisted.status is ActionIntentStatus.PENDING
    assert persisted.available_at == expected_retry
    assert persisted.last_error_code == "adapter_exception"
