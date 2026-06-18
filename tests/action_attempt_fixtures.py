from datetime import timedelta

from osoznanie.action_attempt_store import SQLiteActionAttemptStore

from .action_outbox_fixtures import T0, make_outbox, make_proposal, prepare_trace


def claimed_attempt_context(suffix: str = "one"):
    store, _, outbox = make_outbox()
    trace = prepare_trace(store, suffix)
    outbox.enqueue(trace, make_proposal())
    worker = f"worker-{suffix}"
    claimed = outbox.claim(
        worker,
        T0 + timedelta(minutes=1),
        timedelta(minutes=10),
    )
    assert claimed is not None
    attempt_store = SQLiteActionAttemptStore(store, outbox)
    started = attempt_store.start(
        claimed,
        worker,
        claimed.lease_token or "",
        T0 + timedelta(minutes=2),
    )
    return store, outbox, attempt_store, claimed, started
