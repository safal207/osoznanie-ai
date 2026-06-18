import sqlite3

import pytest

from osoznanie.action_outbox import (
    ActionIntentContractError,
    OutboxIdempotencyConflictError,
    build_action_intent,
)
from osoznanie.sqlite_action_outbox import SQLiteActionOutbox

from .action_outbox_fixtures import make_outbox, make_proposal, prepare_trace


def test_enqueue_atomically_persists_trace_and_intent() -> None:
    _, trace_store, outbox = make_outbox()
    trace = prepare_trace(outbox.store)

    intent = outbox.enqueue(trace, make_proposal())

    assert trace_store.get(trace.id) == trace
    assert outbox.get(intent.id) == intent
    assert intent.trace_id == trace.id


def test_enqueue_is_idempotent() -> None:
    _, trace_store, outbox = make_outbox()
    trace = prepare_trace(outbox.store)

    first = outbox.enqueue(trace, make_proposal())
    second = outbox.enqueue(trace, make_proposal())

    assert first == second
    assert trace_store.list() == [trace]


class BrokenInsertOutbox(SQLiteActionOutbox):
    def _insert_intent(self, connection, intent) -> None:
        raise sqlite3.IntegrityError("simulated outbox insert failure")


def test_outbox_failure_rolls_back_new_trace() -> None:
    store, trace_store, _ = make_outbox()
    outbox = BrokenInsertOutbox(store, trace_store)
    trace = prepare_trace(store)

    with pytest.raises(OutboxIdempotencyConflictError):
        outbox.enqueue(trace, make_proposal())

    assert not trace_store.exists(trace.id)


def test_conflicting_intent_for_same_trace_is_rejected() -> None:
    store, trace_store, outbox = make_outbox()
    trace = prepare_trace(store)
    trace_store.save(trace)
    expected = build_action_intent(trace, make_proposal())
    conflict = expected.model_copy(
        update={
            "id": "act_conflict",
            "tool_name": "different.tool",
            "idempotency_key": "osi_conflict",
        }
    )
    with store._connect() as connection:
        outbox._insert_intent(connection, conflict)

    with pytest.raises(OutboxIdempotencyConflictError):
        outbox.enqueue(trace, make_proposal())


def test_mismatched_dispatch_metadata_is_rejected_before_trace_save() -> None:
    _, trace_store, outbox = make_outbox()
    trace = prepare_trace(outbox.store)

    with pytest.raises(ActionIntentContractError):
        outbox.enqueue(
            trace,
            make_proposal(tool_name="different.tool"),
        )

    assert not trace_store.exists(trace.id)
