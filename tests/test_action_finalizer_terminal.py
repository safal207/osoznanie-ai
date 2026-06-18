from datetime import timedelta

import pytest

from osoznanie.action_finalizer import (
    ActionFinalizationConflictError,
    ActionFinalizationStatus,
    SQLiteActionFinalizer,
)
from osoznanie.action_outbox import ActionIntentStatus

from .action_attempt_fixtures import claimed_attempt_context
from .action_outbox_fixtures import T0


def test_permanent_failure_is_terminal_and_idempotent() -> None:
    store, outbox, attempt_store, intent, started = claimed_attempt_context()
    finalizer = SQLiteActionFinalizer(store, outbox, attempt_store)

    first = finalizer.fail(
        started,
        intent.lease_token or "",
        T0 + timedelta(minutes=3),
        "permanent_failure",
    )
    second = finalizer.fail(
        started,
        intent.lease_token or "",
        T0 + timedelta(minutes=3),
        "permanent_failure",
    )

    persisted = outbox.get(intent.id)
    assert first.status is ActionFinalizationStatus.FAILED
    assert second.already_finalized is True
    assert persisted.status is ActionIntentStatus.FAILED
    assert persisted.last_error_code == "permanent_failure"
    assert outbox.claim(
        "worker-next",
        T0 + timedelta(days=1),
        timedelta(minutes=5),
    ) is None
    assert attempt_store.list(intent.id) == [started, first.attempt]


def test_conflicting_failure_retry_is_rejected() -> None:
    store, outbox, attempt_store, intent, started = claimed_attempt_context()
    finalizer = SQLiteActionFinalizer(store, outbox, attempt_store)
    finalizer.fail(
        started,
        intent.lease_token or "",
        T0 + timedelta(minutes=3),
        "first_error",
    )

    with pytest.raises(ActionFinalizationConflictError):
        finalizer.fail(
            started,
            intent.lease_token or "",
            T0 + timedelta(minutes=4),
            "different_error",
        )

    assert outbox.get(intent.id).last_error_code == "first_error"
    assert len(attempt_store.list(intent.id)) == 2


def test_expired_lease_rejects_finalization() -> None:
    store, outbox, attempt_store, intent, started = claimed_attempt_context()
    finalizer = SQLiteActionFinalizer(store, outbox, attempt_store)

    with pytest.raises(ActionFinalizationConflictError, match="expired lease"):
        finalizer.fail(
            started,
            intent.lease_token or "",
            T0 + timedelta(minutes=11),
            "late_failure",
        )

    assert outbox.get(intent.id).status is ActionIntentStatus.LEASED
    assert attempt_store.list(intent.id) == [started]
