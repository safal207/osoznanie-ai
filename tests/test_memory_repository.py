from datetime import UTC, datetime, timedelta

import pytest

from osoznanie.consolidation import (
    AmbiguousMemoryHistoryError,
    ConsolidationEngine,
    ConsolidationResult,
    MemoryMutation,
    MemoryMutationKind,
)
from osoznanie.memory import MemoryStatus, MemoryType
from osoznanie.memory_repository import (
    InvalidMemoryProgressionError,
    UnsafeMemoryWriteError,
    VersionConflictError,
)
from osoznanie.models import Event
from osoznanie.storage import MissingReferenceError, SQLiteExperienceStore

NOW = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)


def mutation(
    event_id: str,
    *,
    state: str,
    effective_at: datetime = NOW,
    kind: MemoryMutationKind = MemoryMutationKind.UPSERT,
) -> MemoryMutation:
    return MemoryMutation(
        kind=kind,
        memory_key="trip.singapore.status",
        memory_type=MemoryType.FACT,
        content={"state": state},
        source_event_ids=[event_id],
        confidence=0.9,
        importance=0.7,
        effective_at=effective_at,
    )


def save_event(store: SQLiteExperienceStore, event_id: str) -> Event:
    return store.save(
        Event(
            id=event_id,
            actor_ids=["human_alexey"],
            summary=f"Source event {event_id}",
            occurred_at=NOW,
            created_at=NOW,
        )
    )


def commit_initial(store: SQLiteExperienceStore):
    save_event(store, "evt_plan")
    result = ConsolidationEngine().consolidate(
        mutation("evt_plan", state="planned")
    )
    memory = store.commit_memory(
        result,
        expected_head_id=None,
        expected_version=None,
    )
    return result, memory


def test_commit_version_one_and_read_explicit_head() -> None:
    store = SQLiteExperienceStore()
    result, memory = commit_initial(store)

    head = store.get_memory_head(memory.memory_key)

    assert memory == result.memory
    assert head is not None
    assert head.current_id == memory.id
    assert head.current_version == 1


def test_commit_version_two_with_matching_compare_and_swap() -> None:
    store = SQLiteExperienceStore()
    _, first = commit_initial(store)
    save_event(store, "evt_completed")
    second_result = ConsolidationEngine().consolidate(
        mutation(
            "evt_completed",
            state="completed",
            effective_at=NOW + timedelta(days=10),
        ),
        [first],
    )

    second = store.commit_memory(
        second_result,
        expected_head_id=first.id,
        expected_version=1,
    )
    head = store.get_memory_head(first.memory_key)

    assert second.version == 2
    assert second.supersedes == [first.id]
    assert head is not None
    assert head.current_id == second.id
    assert head.current_version == 2
    assert store.get(first.id) == first


def test_stale_expected_head_is_a_retryable_version_conflict() -> None:
    store = SQLiteExperienceStore()
    _, first = commit_initial(store)
    save_event(store, "evt_completed")
    second_result = ConsolidationEngine().consolidate(
        mutation(
            "evt_completed",
            state="completed",
            effective_at=NOW + timedelta(days=1),
        ),
        [first],
    )

    with pytest.raises(VersionConflictError) as raised:
        store.commit_memory(
            second_result,
            expected_head_id="mem_stale",
            expected_version=1,
        )

    assert raised.value.actual_head_id == first.id
    assert raised.value.actual_version == 1


def test_stale_expected_version_is_a_retryable_version_conflict() -> None:
    store = SQLiteExperienceStore()
    _, first = commit_initial(store)
    save_event(store, "evt_completed")
    second_result = ConsolidationEngine().consolidate(
        mutation(
            "evt_completed",
            state="completed",
            effective_at=NOW + timedelta(days=1),
        ),
        [first],
    )

    with pytest.raises(VersionConflictError) as raised:
        store.commit_memory(
            second_result,
            expected_head_id=first.id,
            expected_version=0,
        )

    assert raised.value.expected_version == 0
    assert raised.value.actual_version == 1


def test_exact_duplicate_delivery_is_idempotent() -> None:
    store = SQLiteExperienceStore()
    _, first = commit_initial(store)
    save_event(store, "evt_completed")
    result = ConsolidationEngine().consolidate(
        mutation(
            "evt_completed",
            state="completed",
            effective_at=NOW + timedelta(days=1),
        ),
        [first],
    )

    committed = store.commit_memory(
        result,
        expected_head_id=first.id,
        expected_version=1,
    )
    delivered_again = store.commit_memory(
        result,
        expected_head_id=first.id,
        expected_version=1,
    )

    assert delivered_again == committed
    assert store.get_memory_head(first.memory_key).current_version == 2  # type: ignore[union-attr]
    assert [item.id for item in store.list("memory")] == [first.id, committed.id]


def test_skipped_version_is_rejected_without_changing_head() -> None:
    store = SQLiteExperienceStore()
    _, first = commit_initial(store)
    save_event(store, "evt_completed")
    valid_result = ConsolidationEngine().consolidate(
        mutation(
            "evt_completed",
            state="completed",
            effective_at=NOW + timedelta(days=1),
        ),
        [first],
    )
    invalid_memory = valid_result.memory.model_copy(update={"version": 3})
    invalid_result = ConsolidationResult(
        memory=invalid_memory,
        previous_memory_id=first.id,
        mutation_kind=MemoryMutationKind.UPSERT,
        change_summary="Invalid skipped version.",
    )

    with pytest.raises(InvalidMemoryProgressionError, match="advance"):
        store.commit_memory(
            invalid_result,
            expected_head_id=first.id,
            expected_version=1,
        )

    assert store.get_memory_head(first.memory_key).current_id == first.id  # type: ignore[union-attr]


def test_wrong_supersedes_link_is_rejected() -> None:
    store = SQLiteExperienceStore()
    _, first = commit_initial(store)
    save_event(store, "evt_completed")
    valid_result = ConsolidationEngine().consolidate(
        mutation(
            "evt_completed",
            state="completed",
            effective_at=NOW + timedelta(days=1),
        ),
        [first],
    )
    invalid_memory = valid_result.memory.model_copy(
        update={"supersedes": ["mem_wrong"]}
    )
    invalid_result = ConsolidationResult(
        memory=invalid_memory,
        previous_memory_id=first.id,
        mutation_kind=MemoryMutationKind.UPSERT,
        change_summary="Invalid supersession.",
    )

    with pytest.raises(InvalidMemoryProgressionError, match="supersede"):
        store.commit_memory(
            invalid_result,
            expected_head_id=first.id,
            expected_version=1,
        )


def test_missing_source_provenance_rolls_back_entire_commit() -> None:
    store = SQLiteExperienceStore()
    result = ConsolidationEngine().consolidate(
        mutation("evt_missing", state="planned")
    )

    with pytest.raises(MissingReferenceError, match="evt_missing"):
        store.commit_memory(
            result,
            expected_head_id=None,
            expected_version=None,
        )

    assert store.get_memory_head(result.memory.memory_key) is None
    assert not store.exists(result.memory.id)


def test_later_memory_versions_cannot_bypass_atomic_commit() -> None:
    store = SQLiteExperienceStore()
    _, first = commit_initial(store)
    save_event(store, "evt_revoke")
    revoked = ConsolidationEngine().consolidate(
        mutation(
            "evt_revoke",
            state="revoked",
            effective_at=NOW + timedelta(hours=1),
            kind=MemoryMutationKind.REVOKE,
        ),
        [first],
    ).memory

    assert revoked.status is MemoryStatus.REVOKED
    with pytest.raises(UnsafeMemoryWriteError, match="commit_memory"):
        store.save(revoked)


def test_independent_connections_reject_a_stale_writer(tmp_path) -> None:
    database = tmp_path / "memory.db"
    first_writer = SQLiteExperienceStore(database)
    stale_writer = SQLiteExperienceStore(database)
    _, first = commit_initial(first_writer)
    save_event(first_writer, "evt_a")
    save_event(first_writer, "evt_b")

    writer_a_result = ConsolidationEngine().consolidate(
        mutation(
            "evt_a",
            state="booked",
            effective_at=NOW + timedelta(hours=1),
        ),
        [first],
    )
    writer_b_result = ConsolidationEngine().consolidate(
        mutation(
            "evt_b",
            state="cancelled",
            effective_at=NOW + timedelta(hours=1),
        ),
        [first],
    )

    winner = first_writer.commit_memory(
        writer_a_result,
        expected_head_id=first.id,
        expected_version=1,
    )
    with pytest.raises(VersionConflictError) as raised:
        stale_writer.commit_memory(
            writer_b_result,
            expected_head_id=first.id,
            expected_version=1,
        )

    assert raised.value.actual_head_id == winner.id
    assert raised.value.actual_version == 2
    assert stale_writer.get_memory_head(first.memory_key).current_id == winner.id  # type: ignore[union-attr]


def test_database_unique_index_detects_duplicate_memory_version() -> None:
    store = SQLiteExperienceStore()
    _, first = commit_initial(store)
    save_event(store, "evt_duplicate")
    duplicate = first.model_copy(
        update={
            "id": "mem_duplicate",
            "source_event_ids": ["evt_duplicate"],
            "content": {"state": "different"},
        }
    )

    with store._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            store._insert_record(connection, duplicate)
            with pytest.raises(Exception):
                connection.execute(
                    """
                    INSERT INTO memory_versions (memory_id, memory_key, version)
                    VALUES (?, ?, ?)
                    """,
                    (duplicate.id, duplicate.memory_key, duplicate.version),
                )
        finally:
            connection.execute("ROLLBACK")

    assert store.get_memory_head(first.memory_key).current_id == first.id  # type: ignore[union-attr]
