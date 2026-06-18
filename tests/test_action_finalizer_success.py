from datetime import timedelta

import pytest

from osoznanie.action_finalizer import (
    ActionFinalizationConflictError,
    ActionFinalizationStatus,
    SQLiteActionFinalizer,
)
from osoznanie.action_outbox import ActionIntentStatus

from .action_attempt_fixtures import claimed_attempt_context
from .action_outbox_fixtures import T0, save_outcome


def test_success_commits_attempt_and_outbox_together() -> None:
    store, outbox, attempt_store, intent, started = claimed_attempt_context()
    outcome = save_outcome(
        store,
        observed_at=T0 + timedelta(minutes=3),
    )
    finalizer = SQLiteActionFinalizer(store, outbox, attempt_store)

    result = finalizer.complete(
        started,
        intent.lease_token or "",
        T0 + timedelta(minutes=3),
        outcome.id,
        response_hash="sha256:response",
    )

    persisted_intent = outbox.get(intent.id)
    assert result.status is ActionFinalizationStatus.COMPLETED
    assert result.already_finalized is False
    assert persisted_intent.status is ActionIntentStatus.COMPLETED
    assert persisted_intent.outcome_id == outcome.id
    assert finalizer.get_last_attempt_id(intent.id) == result.attempt.id
    assert attempt_store.list(intent.id) == [started, result.attempt]


def test_identical_success_retry_is_idempotent() -> None:
    store, outbox, attempt_store, intent, started = claimed_attempt_context()
    outcome = save_outcome(
        store,
        observed_at=T0 + timedelta(minutes=3),
    )
    finalizer = SQLiteActionFinalizer(store, outbox, attempt_store)

    first = finalizer.complete(
        started,
        intent.lease_token or "",
        T0 + timedelta(minutes=3),
        outcome.id,
    )
    second = finalizer.complete(
        started,
        intent.lease_token or "",
        T0 + timedelta(minutes=3),
        outcome.id,
    )

    assert first.attempt == second.attempt
    assert second.already_finalized is True
    assert attempt_store.list(intent.id) == [started, first.attempt]


def test_wrong_lease_token_rejects_without_terminal_evidence() -> None:
    store, outbox, attempt_store, intent, started = claimed_attempt_context()
    outcome = save_outcome(
        store,
        observed_at=T0 + timedelta(minutes=3),
    )
    finalizer = SQLiteActionFinalizer(store, outbox, attempt_store)

    with pytest.raises(ActionFinalizationConflictError, match="lease token"):
        finalizer.complete(
            started,
            "wrong-token",
            T0 + timedelta(minutes=3),
            outcome.id,
        )

    assert outbox.get(intent.id).status is ActionIntentStatus.LEASED
    assert finalizer.get_last_attempt_id(intent.id) is None
    assert attempt_store.list(intent.id) == [started]


def test_conflicting_success_retry_fails_closed() -> None:
    store, outbox, attempt_store, intent, started = claimed_attempt_context()
    first_outcome = save_outcome(
        store,
        "first",
        observed_at=T0 + timedelta(minutes=3),
    )
    second_outcome = save_outcome(
        store,
        "second",
        observed_at=T0 + timedelta(minutes=4),
    )
    finalizer = SQLiteActionFinalizer(store, outbox, attempt_store)
    finalizer.complete(
        started,
        intent.lease_token or "",
        T0 + timedelta(minutes=3),
        first_outcome.id,
    )

    with pytest.raises(ActionFinalizationConflictError):
        finalizer.complete(
            started,
            intent.lease_token or "",
            T0 + timedelta(minutes=4),
            second_outcome.id,
        )

    assert outbox.get(intent.id).outcome_id == first_outcome.id
    assert len(attempt_store.list(intent.id)) == 2
