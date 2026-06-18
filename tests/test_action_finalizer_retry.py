from datetime import timedelta

from osoznanie.action_finalizer import (
    ActionFinalizationStatus,
    SQLiteActionFinalizer,
)
from osoznanie.action_outbox import ActionIntentStatus

from .action_attempt_fixtures import claimed_attempt_context
from .action_outbox_fixtures import T0


def test_retryable_failure_commits_attempt_and_retry_state() -> None:
    store, outbox, attempt_store, intent, started = claimed_attempt_context()
    finalizer = SQLiteActionFinalizer(store, outbox, attempt_store)
    retry_at = T0 + timedelta(minutes=10)

    result = finalizer.fail(
        started,
        intent.lease_token or "",
        T0 + timedelta(minutes=3),
        "temporary_failure",
        retry_at=retry_at,
    )

    persisted = outbox.get(intent.id)
    assert result.status is ActionFinalizationStatus.RETRY_SCHEDULED
    assert persisted.status is ActionIntentStatus.PENDING
    assert persisted.available_at == retry_at
    assert persisted.last_error_code == "temporary_failure"
    assert finalizer.get_last_attempt_id(intent.id) == result.attempt.id
    assert outbox.claim(
        "worker-early",
        retry_at - timedelta(seconds=1),
        timedelta(minutes=5),
    ) is None
    reclaimed = outbox.claim(
        "worker-next",
        retry_at,
        timedelta(minutes=5),
    )
    assert reclaimed is not None
    assert reclaimed.attempt_count == 2


def test_identical_retryable_failure_is_idempotent() -> None:
    store, outbox, attempt_store, intent, started = claimed_attempt_context()
    finalizer = SQLiteActionFinalizer(store, outbox, attempt_store)
    retry_at = T0 + timedelta(minutes=10)

    first = finalizer.fail(
        started,
        intent.lease_token or "",
        T0 + timedelta(minutes=3),
        "temporary_failure",
        retry_at=retry_at,
    )
    second = finalizer.fail(
        started,
        intent.lease_token or "",
        T0 + timedelta(minutes=3),
        "temporary_failure",
        retry_at=retry_at,
    )

    assert first.attempt == second.attempt
    assert second.already_finalized is True
    assert attempt_store.list(intent.id) == [started, first.attempt]
