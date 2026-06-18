from datetime import timedelta

import pytest

from osoznanie.action_attempt import ActionAttempt
from osoznanie.action_finalizer import SQLiteActionFinalizer
from osoznanie.action_outbox import ActionIntentStatus

from .action_attempt_fixtures import claimed_attempt_context
from .action_outbox_fixtures import T0, save_outcome


class HookFinalizer(SQLiteActionFinalizer):
    def _after_attempt_saved(self, connection, attempt: ActionAttempt) -> None:
        del connection, attempt
        raise RuntimeError("stop")


def test_transaction_rolls_back_both_records() -> None:
    store, outbox, attempt_store, intent, started = claimed_attempt_context()
    outcome = save_outcome(store, observed_at=T0 + timedelta(minutes=3))
    finalizer = HookFinalizer(store, outbox, attempt_store)

    with pytest.raises(RuntimeError, match="stop"):
        finalizer.complete(
            started,
            intent.lease_token or "",
            T0 + timedelta(minutes=3),
            outcome.id,
        )

    persisted = outbox.get(intent.id)
    assert persisted.status is ActionIntentStatus.LEASED
    assert persisted.outcome_id is None
    assert finalizer.get_last_attempt_id(intent.id) is None
    assert attempt_store.list(intent.id) == [started]
