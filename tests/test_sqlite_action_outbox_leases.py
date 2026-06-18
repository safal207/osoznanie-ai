from datetime import datetime, timedelta

import pytest

from osoznanie.action_outbox import (
    ActionIntentStatus,
    LeaseConflictError,
    TerminalActionIntentError,
)
from osoznanie.storage import MissingReferenceError

from .action_outbox_fixtures import (
    T0,
    make_outbox,
    make_proposal,
    prepare_trace,
    save_outcome,
)


def enqueue_one(suffix: str = "one"):
    store, trace_store, outbox = make_outbox()
    trace = prepare_trace(store, suffix)
    intent = outbox.enqueue(trace, make_proposal())
    return store, trace_store, outbox, intent


def test_non_expired_lease_cannot_be_stolen() -> None:
    _, _, outbox, intent = enqueue_one()
    first = outbox.claim("worker-a", T0 + timedelta(minutes=1), timedelta(minutes=5))

    assert first is not None
    assert first.id == intent.id
    assert first.status is ActionIntentStatus.LEASED
    assert first.attempt_count == 1
    assert outbox.claim(
        "worker-b",
        T0 + timedelta(minutes=2),
        timedelta(minutes=5),
    ) is None


def test_expired_lease_is_reclaimed_with_new_token() -> None:
    _, _, outbox, _ = enqueue_one()
    first = outbox.claim("worker-a", T0 + timedelta(minutes=1), timedelta(minutes=5))
    assert first is not None

    second = outbox.claim("worker-b", T0 + timedelta(minutes=6), timedelta(minutes=5))

    assert second is not None
    assert second.lease_owner == "worker-b"
    assert second.lease_token != first.lease_token
    assert second.attempt_count == 2


def test_wrong_or_expired_token_cannot_complete() -> None:
    store, _, outbox, _ = enqueue_one()
    claimed = outbox.claim("worker-a", T0 + timedelta(minutes=1), timedelta(minutes=5))
    outcome = save_outcome(store)
    assert claimed is not None

    with pytest.raises(LeaseConflictError):
        outbox.complete(
            claimed.id,
            "wrong-token",
            outcome.id,
            T0 + timedelta(minutes=2),
        )
    with pytest.raises(LeaseConflictError):
        outbox.complete(
            claimed.id,
            claimed.lease_token or "",
            outcome.id,
            T0 + timedelta(minutes=6),
        )


def test_completion_is_terminal() -> None:
    store, _, outbox, _ = enqueue_one()
    claimed = outbox.claim("worker-a", T0 + timedelta(minutes=1), timedelta(minutes=5))
    outcome = save_outcome(store)
    assert claimed is not None

    completed = outbox.complete(
        claimed.id,
        claimed.lease_token or "",
        outcome.id,
        T0 + timedelta(minutes=2),
    )

    assert completed.status is ActionIntentStatus.COMPLETED
    assert completed.outcome_id == outcome.id
    assert outbox.claim(
        "worker-b",
        T0 + timedelta(minutes=3),
        timedelta(minutes=5),
    ) is None
    with pytest.raises(TerminalActionIntentError):
        outbox.fail(
            completed.id,
            "stale",
            "tool_error",
            T0 + timedelta(minutes=3),
        )


def test_missing_outcome_rolls_back_completion() -> None:
    _, _, outbox, _ = enqueue_one()
    claimed = outbox.claim("worker-a", T0 + timedelta(minutes=1), timedelta(minutes=5))
    assert claimed is not None

    with pytest.raises(MissingReferenceError):
        outbox.complete(
            claimed.id,
            claimed.lease_token or "",
            "out_missing",
            T0 + timedelta(minutes=2),
        )

    persisted = outbox.get(claimed.id)
    assert persisted.status is ActionIntentStatus.LEASED
    assert persisted.lease_token == claimed.lease_token


def test_retryable_failure_returns_to_pending_at_retry_time() -> None:
    _, _, outbox, _ = enqueue_one()
    claimed = outbox.claim("worker-a", T0 + timedelta(minutes=1), timedelta(minutes=5))
    assert claimed is not None
    retry_at = T0 + timedelta(minutes=10)

    pending = outbox.fail(
        claimed.id,
        claimed.lease_token or "",
        "temporary_failure",
        T0 + timedelta(minutes=2),
        retry_at=retry_at,
    )

    assert pending.status is ActionIntentStatus.PENDING
    assert pending.last_error_code == "temporary_failure"
    assert outbox.list_ready(retry_at - timedelta(seconds=1)) == []
    assert [item.id for item in outbox.list_ready(retry_at)] == [pending.id]
    reclaimed = outbox.claim("worker-b", retry_at, timedelta(minutes=5))
    assert reclaimed is not None
    assert reclaimed.attempt_count == 2


def test_permanent_failure_is_terminal() -> None:
    _, _, outbox, _ = enqueue_one()
    claimed = outbox.claim("worker-a", T0 + timedelta(minutes=1), timedelta(minutes=5))
    assert claimed is not None

    failed = outbox.fail(
        claimed.id,
        claimed.lease_token or "",
        "permanent_failure",
        T0 + timedelta(minutes=2),
    )

    assert failed.status is ActionIntentStatus.FAILED
    assert outbox.list_ready(T0 + timedelta(days=1)) == []
    with pytest.raises(TerminalActionIntentError):
        outbox.fail(
            failed.id,
            "stale",
            "again",
            T0 + timedelta(minutes=3),
        )


def test_ready_order_is_available_then_created_then_id() -> None:
    store, _, outbox = make_outbox()
    later = T0 + timedelta(minutes=10)
    first = prepare_trace(store, "first", created_at=T0, decision_at=later)
    second = prepare_trace(
        store,
        "second",
        created_at=T0 + timedelta(seconds=1),
        decision_at=later,
    )
    early = prepare_trace(
        store,
        "early",
        created_at=T0 + timedelta(seconds=2),
        decision_at=T0 + timedelta(minutes=5),
    )
    first_intent = outbox.enqueue(first, make_proposal())
    second_intent = outbox.enqueue(second, make_proposal())
    early_intent = outbox.enqueue(early, make_proposal())

    ready = outbox.list_ready(later)

    assert [item.id for item in ready] == [
        early_intent.id,
        first_intent.id,
        second_intent.id,
    ]


def test_time_and_lease_validation() -> None:
    _, _, outbox, _ = enqueue_one()

    with pytest.raises(ValueError, match="timezone-aware"):
        outbox.list_ready(datetime(2026, 6, 18, 12, 0))
    with pytest.raises(ValueError, match="positive"):
        outbox.claim("worker", T0, timedelta(0))
