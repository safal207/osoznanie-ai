from datetime import timedelta

import pytest

from osoznanie.action_attempt import ActionAttemptStatus, build_succeeded_attempt
from osoznanie.storage import DuplicateRecordError, MissingReferenceError

from .action_attempt_fixtures import claimed_attempt_context
from .action_outbox_fixtures import T0, save_outcome


def test_started_save_is_idempotent() -> None:
    _, _, attempt_store, _, started = claimed_attempt_context()

    assert attempt_store.save(started) == started
    assert attempt_store.get(started.id) == started
    assert attempt_store.list(started.intent_id) == [started]


def test_success_revision_links_outcome_without_mutation() -> None:
    store, _, attempt_store, _, started = claimed_attempt_context()
    outcome = save_outcome(
        store,
        observed_at=T0 + timedelta(minutes=4),
    )

    succeeded = attempt_store.succeed(
        started,
        T0 + timedelta(minutes=4),
        outcome.id,
        response_hash="sha256:response",
    )

    assert succeeded.status is ActionAttemptStatus.SUCCEEDED
    assert succeeded.outcome_id == outcome.id
    assert attempt_store.list(started.intent_id) == [started, succeeded]
    assert attempt_store.get(started.id) == started


def test_second_terminal_revision_is_rejected() -> None:
    store, _, attempt_store, _, started = claimed_attempt_context()
    first = save_outcome(store, "first", observed_at=T0 + timedelta(minutes=3))
    second = save_outcome(store, "second", observed_at=T0 + timedelta(minutes=4))
    attempt_store.succeed(started, T0 + timedelta(minutes=3), first.id)
    conflicting = build_succeeded_attempt(
        started,
        T0 + timedelta(minutes=4),
        second.id,
    )

    with pytest.raises(DuplicateRecordError):
        attempt_store.save(conflicting)


def test_missing_outcome_rolls_back_terminal_save() -> None:
    _, _, attempt_store, _, started = claimed_attempt_context()
    terminal = build_succeeded_attempt(
        started,
        T0 + timedelta(minutes=3),
        "out_missing",
    )

    with pytest.raises(MissingReferenceError):
        attempt_store.save(terminal)
    assert attempt_store.list(started.intent_id) == [started]
