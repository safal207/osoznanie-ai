from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from osoznanie.consolidation import (
    AmbiguousMemoryHistoryError,
    ConsolidationEngine,
    MemoryMutation,
    MemoryMutationKind,
    MemoryTypeChangeError,
    MissingMemoryHistoryError,
)
from osoznanie.memory import MemoryObject, MemoryStatus, MemoryType

NOW = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)


def mutation(**overrides) -> MemoryMutation:
    values = {
        "kind": MemoryMutationKind.UPSERT,
        "memory_key": "trip.singapore.status",
        "memory_type": MemoryType.FACT,
        "content": {"state": "planned"},
        "source_event_ids": ["evt_trip_plan"],
        "confidence": 0.9,
        "importance": 0.7,
        "effective_at": NOW,
    }
    values.update(overrides)
    return MemoryMutation(**values)


def test_create_version_one_from_source_backed_mutation() -> None:
    result = ConsolidationEngine().consolidate(mutation())

    assert result.previous_memory_id is None
    assert result.memory.version == 1
    assert result.memory.status is MemoryStatus.ACTIVE
    assert result.memory.supersedes == []
    assert result.memory.source_event_ids == ["evt_trip_plan"]
    assert result.change_summary == "Created trip.singapore.status as version 1."


def test_same_inputs_produce_the_same_memory() -> None:
    engine = ConsolidationEngine()

    left = engine.consolidate(mutation())
    right = engine.consolidate(mutation())

    assert left == right
    assert left.memory.canonical_json() == right.memory.canonical_json()


def test_update_creates_version_two_and_preserves_version_one() -> None:
    engine = ConsolidationEngine()
    first = engine.consolidate(mutation()).memory
    original = first.model_copy(deep=True)

    second = engine.consolidate(
        mutation(
            content={"state": "completed"},
            source_event_ids=["evt_trip_completed"],
            effective_at=NOW + timedelta(days=10),
        ),
        [first],
    ).memory

    assert second.version == 2
    assert second.supersedes == [first.id]
    assert second.content == {"state": "completed"}
    assert first == original


def test_revoke_requires_and_supersedes_existing_memory() -> None:
    engine = ConsolidationEngine()
    first = engine.consolidate(mutation()).memory

    revoked = engine.consolidate(
        mutation(
            kind=MemoryMutationKind.REVOKE,
            content={"reason": "user withdrew the memory"},
            source_event_ids=["evt_revoke"],
            effective_at=NOW + timedelta(hours=1),
        ),
        [first],
    ).memory

    assert revoked.status is MemoryStatus.REVOKED
    assert revoked.version == 2
    assert revoked.supersedes == [first.id]
    assert not revoked.is_active_at(NOW + timedelta(hours=1))


def test_dispute_links_external_contradiction() -> None:
    engine = ConsolidationEngine()
    first = engine.consolidate(mutation()).memory

    disputed = engine.consolidate(
        mutation(
            kind=MemoryMutationKind.DISPUTE,
            content={"state": "uncertain"},
            source_event_ids=["evt_conflict"],
            contradicts=["mem_external_claim"],
            effective_at=NOW + timedelta(hours=2),
        ),
        [first],
    ).memory

    assert disputed.status is MemoryStatus.DISPUTED
    assert disputed.supersedes == [first.id]
    assert disputed.contradicts == ["mem_external_claim"]


def test_revoke_without_history_is_rejected() -> None:
    with pytest.raises(MissingMemoryHistoryError, match="requires existing memory"):
        ConsolidationEngine().consolidate(
            mutation(kind=MemoryMutationKind.REVOKE)
        )


def test_memory_type_change_is_rejected() -> None:
    engine = ConsolidationEngine()
    first = engine.consolidate(mutation()).memory

    with pytest.raises(MemoryTypeChangeError, match="cannot change"):
        engine.consolidate(
            mutation(memory_type=MemoryType.GOAL),
            [first],
        )


def test_mutation_without_source_events_is_rejected() -> None:
    with pytest.raises(ValidationError, match="source_event_ids"):
        mutation(source_event_ids=[])


def test_unordered_history_uses_latest_version_and_ignores_unrelated_memory() -> None:
    engine = ConsolidationEngine()
    first = engine.consolidate(mutation()).memory
    second = engine.consolidate(
        mutation(
            content={"state": "booked"},
            source_event_ids=["evt_booked"],
            effective_at=NOW + timedelta(days=1),
        ),
        [first],
    ).memory
    unrelated = MemoryObject(
        memory_key="career.target",
        memory_type=MemoryType.GOAL,
        content={"role": "AI safety researcher"},
        source_event_ids=["evt_career"],
        confidence=0.8,
        importance=0.9,
        valid_from=NOW,
        created_at=NOW,
        updated_at=NOW,
    )

    third = engine.consolidate(
        mutation(
            content={"state": "completed"},
            source_event_ids=["evt_completed"],
            effective_at=NOW + timedelta(days=2),
        ),
        [unrelated, second, first],
    ).memory

    assert third.version == 3
    assert third.supersedes == [second.id]


def test_duplicate_version_history_is_rejected_as_ambiguous() -> None:
    engine = ConsolidationEngine()
    first = engine.consolidate(mutation()).memory
    duplicate_version = first.model_copy(update={"id": "mem_duplicate_version"})

    with pytest.raises(AmbiguousMemoryHistoryError, match="multiple memory records"):
        engine.consolidate(
            mutation(
                content={"state": "completed"},
                source_event_ids=["evt_completed"],
                effective_at=NOW + timedelta(days=2),
            ),
            [duplicate_version, first],
        )


def test_equivalent_instants_are_normalized_before_id_generation() -> None:
    engine = ConsolidationEngine()
    utc_result = engine.consolidate(mutation())
    plus_three_result = engine.consolidate(
        mutation(effective_at=datetime(2026, 6, 18, 15, 0, tzinfo=timezone(timedelta(hours=3))))
    )

    assert utc_result.memory.id == plus_three_result.memory.id
    assert utc_result.memory.valid_from == plus_three_result.memory.valid_from
