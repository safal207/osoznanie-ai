from datetime import timedelta

import pytest

from osoznanie.action_attempt import (
    ActionAttemptContractError,
    ActionAttemptStatus,
    build_failed_attempt,
    build_started_attempt,
    build_succeeded_attempt,
)

from .action_attempt_fixtures import claimed_attempt_context
from .action_outbox_fixtures import T0


def test_started_attempt_is_deterministic_and_private() -> None:
    _, _, _, intent, _ = claimed_attempt_context()
    started_at = T0 + timedelta(minutes=2)
    left = build_started_attempt(
        intent,
        "worker-one",
        intent.lease_token or "",
        started_at,
    )
    right = build_started_attempt(
        intent,
        "worker-one",
        intent.lease_token or "",
        started_at,
    )

    assert left == right
    assert left.status is ActionAttemptStatus.STARTED
    assert left.attempt_number == intent.attempt_count
    assert left.lease_token_hash.startswith("sha256:")
    assert intent.lease_token not in left.model_dump_json()


def test_started_attempt_requires_matching_live_lease() -> None:
    _, _, _, intent, _ = claimed_attempt_context()

    with pytest.raises(ActionAttemptContractError):
        build_started_attempt(
            intent,
            "different-worker",
            intent.lease_token or "",
            T0 + timedelta(minutes=2),
        )
    with pytest.raises(ActionAttemptContractError):
        build_started_attempt(
            intent,
            "worker-one",
            intent.lease_token or "",
            T0 + timedelta(minutes=11),
        )


def test_terminal_builders_append_revision_and_latency() -> None:
    _, _, _, _, started = claimed_attempt_context()
    succeeded = build_succeeded_attempt(
        started,
        T0 + timedelta(minutes=2, seconds=1, milliseconds=250),
        "out_success",
    )
    failed = build_failed_attempt(
        started,
        T0 + timedelta(minutes=2, seconds=2),
        "tool_timeout",
    )

    assert succeeded.revision == 2
    assert succeeded.supersedes_attempt_id == started.id
    assert succeeded.latency_ms == 1250
    assert failed.status is ActionAttemptStatus.FAILED
    assert failed.latency_ms == 2000
    assert failed.error_code == "tool_timeout"
