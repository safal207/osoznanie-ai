from datetime import timedelta

import pytest

from osoznanie.action_attempt import (
    ActionAttemptContractError,
    ActionAttemptStatus,
)
from osoznanie.action_attempt_store import SQLiteActionAttemptStore

from .action_attempt_fixtures import claimed_attempt_context
from .action_outbox_fixtures import T0, make_outbox, make_proposal, prepare_trace


def test_failure_revision_is_terminal_evidence() -> None:
    _, _, attempt_store, _, started = claimed_attempt_context()

    failed = attempt_store.fail(
        started,
        T0 + timedelta(minutes=3),
        "tool_timeout",
        response_hash="sha256:error-response",
    )

    assert failed.status is ActionAttemptStatus.FAILED
    assert failed.error_code == "tool_timeout"
    assert failed.outcome_id is None
    assert attempt_store.list(started.intent_id) == [started, failed]


def test_changed_payload_under_same_id_is_rejected() -> None:
    _, _, attempt_store, _, started = claimed_attempt_context()
    changed = started.model_copy(update={"worker_id": "other-worker"})

    with pytest.raises(ActionAttemptContractError):
        attempt_store.save(changed)


def test_listing_order_uses_started_time() -> None:
    store, _, outbox = make_outbox()
    attempt_store = SQLiteActionAttemptStore(store, outbox)

    later_trace = prepare_trace(store, "later")
    outbox.enqueue(later_trace, make_proposal())
    later_claim = outbox.claim(
        "worker-later",
        T0 + timedelta(minutes=1),
        timedelta(minutes=10),
    )
    assert later_claim is not None
    later = attempt_store.start(
        later_claim,
        "worker-later",
        later_claim.lease_token or "",
        T0 + timedelta(minutes=3),
    )

    earlier_trace = prepare_trace(store, "earlier")
    outbox.enqueue(earlier_trace, make_proposal())
    earlier_claim = outbox.claim(
        "worker-earlier",
        T0 + timedelta(minutes=1, seconds=30),
        timedelta(minutes=10),
    )
    assert earlier_claim is not None
    earlier = attempt_store.start(
        earlier_claim,
        "worker-earlier",
        earlier_claim.lease_token or "",
        T0 + timedelta(minutes=2),
    )

    assert [item.id for item in attempt_store.list()] == [earlier.id, later.id]
