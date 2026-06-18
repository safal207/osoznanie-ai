from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from osoznanie.memory import (
    MemoryObject,
    MemoryStatus,
    MemoryType,
    resolve_active_memory,
)
from osoznanie.models import Event
from osoznanie.storage import MissingReferenceError, SQLiteExperienceStore

NOW = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)


def fact(**overrides):
    values = {
        "memory_key": "trip.singapore.status",
        "memory_type": MemoryType.FACT,
        "content": {"state": "planned"},
        "source_event_ids": ["evt_plan"],
        "confidence": 0.9,
        "importance": 0.7,
        "valid_from": NOW,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return MemoryObject(**values)


def test_active_fact_requires_provenance() -> None:
    with pytest.raises(ValidationError, match="source_event_ids"):
        fact(source_event_ids=[])


def test_temporal_boundary_is_end_exclusive() -> None:
    memory = fact(valid_until=NOW + timedelta(days=1))
    assert memory.is_active_at(NOW)
    assert not memory.is_active_at(NOW + timedelta(days=1))


def test_new_version_preserves_old_history_and_becomes_active() -> None:
    old = fact(status=MemoryStatus.OUTDATED)
    new = fact(
        content={"state": "completed"},
        source_event_ids=["evt_completed"],
        supersedes=[old.id],
        version=2,
        valid_from=NOW + timedelta(days=10),
        created_at=NOW + timedelta(days=10),
        updated_at=NOW + timedelta(days=10),
    )

    assert resolve_active_memory([old, new], memory_key=old.memory_key, at=NOW) is None
    assert (
        resolve_active_memory(
            [old, new],
            memory_key=old.memory_key,
            at=NOW + timedelta(days=10),
        )
        == new
    )
    assert old.content == {"state": "planned"}


def test_revoked_memory_is_not_active() -> None:
    revoked = fact(status=MemoryStatus.REVOKED)
    assert not revoked.is_active_at(NOW)


def test_deterministic_serialization_ignores_input_reference_order() -> None:
    left = fact(source_event_ids=["evt_b", "evt_a", "evt_a"])
    right = fact(
        id=left.id,
        source_event_ids=["evt_a", "evt_b"],
        created_at=left.created_at,
        updated_at=left.updated_at,
    )
    assert left.canonical_json() == right.canonical_json()


def test_confidence_and_importance_are_bounded() -> None:
    with pytest.raises(ValidationError):
        fact(confidence=1.01)
    with pytest.raises(ValidationError):
        fact(importance=-0.01)


def test_conflict_cannot_also_be_supersession() -> None:
    with pytest.raises(ValidationError, match="both supersede and contradict"):
        fact(version=2, supersedes=["mem_old"], contradicts=["mem_old"])


def test_store_persists_memory_and_explains_event_provenance() -> None:
    store = SQLiteExperienceStore()
    event = store.save(
        Event(
            id="evt_plan",
            actor_ids=["human_alexey"],
            summary="The user is planning a trip to Singapore.",
        )
    )
    memory = fact(source_event_ids=[event.id])

    assert store.save(memory) == memory
    assert store.get(memory.id) == memory
    assert store.list("memory") == [memory]

    explanation = store.explain(memory.id)
    assert explanation["references"][0]["id"] == event.id


def test_store_rejects_missing_memory_provenance() -> None:
    store = SQLiteExperienceStore()
    memory = fact(source_event_ids=["evt_missing"])

    with pytest.raises(MissingReferenceError, match="evt_missing"):
        store.save(memory)
