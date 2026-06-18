from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from osoznanie.consolidation import (
    AmbiguousMemoryHistoryError,
    ConsolidationEngine,
    MemoryMutation,
    MemoryMutationKind,
)
from osoznanie.memory import MemoryObject, MemoryStatus, MemoryType
from osoznanie.memory_view import (
    CommittedMemoryVersion,
    InvalidMemoryTimestampError,
    MemoryViewEngine,
    MemoryViewGateReason,
    MemoryViewQuery,
    MemoryViewReasonCode,
)
from osoznanie.models import Event
from osoznanie.sqlite_memory_view import SQLiteMemoryViewStore
from osoznanie.storage import SQLiteExperienceStore

T0 = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)


class FakeMemoryViewStore:
    def __init__(self, history: list[CommittedMemoryVersion]) -> None:
        self.history = history

    def list_committed_memory_versions(self) -> list[CommittedMemoryVersion]:
        return list(self.history)


def memory(
    *,
    key: str = "trip.singapore.status",
    memory_type: MemoryType = MemoryType.FACT,
    version: int,
    state: str,
    valid_from: datetime,
    committed_at: datetime,
    previous_id: str | None = None,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    valid_until: datetime | None = None,
    memory_id: str | None = None,
) -> CommittedMemoryVersion:
    identifier = memory_id or f"mem_{key.replace('.', '_')}_{version}_{state}"
    return CommittedMemoryVersion(
        memory=MemoryObject(
            id=identifier,
            memory_key=key,
            memory_type=memory_type,
            content={"state": state},
            source_event_ids=[f"evt_{identifier}"],
            confidence=0.9,
            importance=0.7,
            valid_from=valid_from,
            valid_until=valid_until,
            status=status,
            supersedes=[] if version == 1 else [previous_id or "mem_previous"],
            created_at=valid_from,
            updated_at=valid_from,
            version=version,
        ),
        committed_at=committed_at,
    )


def project(
    history: list[CommittedMemoryVersion],
    *,
    as_of: datetime,
    known_at: datetime | None = None,
    memory_keys: list[str] | None = None,
    memory_types: list[MemoryType] | None = None,
):
    return MemoryViewEngine(FakeMemoryViewStore(history)).project(
        MemoryViewQuery(
            as_of=as_of,
            known_at=known_at,
            memory_keys=memory_keys or [],
            memory_types=memory_types or [],
        )
    )


def test_version_one_governs_before_version_two_is_effective() -> None:
    first = memory(
        version=1,
        state="planned",
        valid_from=T0,
        committed_at=T0,
    )
    second = memory(
        version=2,
        state="completed",
        valid_from=T0 + timedelta(days=10),
        committed_at=T0 + timedelta(days=5),
        previous_id=first.memory.id,
    )

    view = project([second, first], as_of=T0 + timedelta(days=5))

    assert [entry.memory.id for entry in view.entries] == [first.memory.id]
    assert view.filter_counts.not_yet_effective == 1
    assert view.filter_counts.superseded_versions == 0


def test_version_two_governs_after_its_effective_time() -> None:
    first = memory(
        version=1,
        state="planned",
        valid_from=T0,
        committed_at=T0,
    )
    second = memory(
        version=2,
        state="completed",
        valid_from=T0 + timedelta(days=10),
        committed_at=T0 + timedelta(days=5),
        previous_id=first.memory.id,
    )

    view = project([first, second], as_of=T0 + timedelta(days=11))

    assert [entry.memory.id for entry in view.entries] == [second.memory.id]
    assert view.filter_counts.superseded_versions == 1
    assert view.entries[0].reason_codes == [
        MemoryViewReasonCode.GOVERNING_VERSION,
        MemoryViewReasonCode.EFFECTIVE_AT_QUERY,
        MemoryViewReasonCode.ACTIVE_STATUS,
        MemoryViewReasonCode.NOT_EXPIRED,
    ]


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (MemoryStatus.REVOKED, MemoryViewGateReason.REVOKED),
        (MemoryStatus.DISPUTED, MemoryViewGateReason.DISPUTED),
        (MemoryStatus.OUTDATED, MemoryViewGateReason.OUTDATED),
    ],
)
def test_non_active_governing_version_never_resurrects_earlier_state(
    status: MemoryStatus,
    reason: MemoryViewGateReason,
) -> None:
    first = memory(
        version=1,
        state="allowed",
        valid_from=T0,
        committed_at=T0,
    )
    second = memory(
        version=2,
        state=status.value,
        valid_from=T0 + timedelta(days=1),
        committed_at=T0 + timedelta(days=1),
        previous_id=first.memory.id,
        status=status,
    )

    view = project([first, second], as_of=T0 + timedelta(days=2))

    assert view.entries == []
    assert len(view.rejections) == 1
    assert view.rejections[0].memory_id == second.memory.id
    assert view.rejections[0].reason is reason
    assert view.filter_counts.non_active_governing == 1
    assert view.filter_counts.superseded_versions == 1


def test_expired_governing_version_does_not_fall_back() -> None:
    first = memory(
        version=1,
        state="allowed",
        valid_from=T0,
        committed_at=T0,
    )
    second = memory(
        version=2,
        state="temporary",
        valid_from=T0 + timedelta(days=1),
        valid_until=T0 + timedelta(days=3),
        committed_at=T0 + timedelta(days=1),
        previous_id=first.memory.id,
    )

    view = project([first, second], as_of=T0 + timedelta(days=3))

    assert view.entries == []
    assert view.rejections[0].reason is MemoryViewGateReason.EXPIRED
    assert view.filter_counts.expired_governing == 1


def test_known_at_excludes_later_backdated_correction() -> None:
    first = memory(
        version=1,
        state="planned",
        valid_from=T0,
        committed_at=T0 + timedelta(days=1),
    )
    second = memory(
        version=2,
        state="cancelled",
        valid_from=T0 + timedelta(days=2),
        committed_at=T0 + timedelta(days=20),
        previous_id=first.memory.id,
    )

    historical = project(
        [second, first],
        as_of=T0 + timedelta(days=10),
        known_at=T0 + timedelta(days=10),
    )
    retrospective = project(
        [second, first],
        as_of=T0 + timedelta(days=10),
        known_at=None,
    )

    assert historical.entries[0].memory.id == first.memory.id
    assert historical.filter_counts.not_known_by_cutoff == 1
    assert MemoryViewReasonCode.KNOWN_BY_CUTOFF in historical.entries[0].reason_codes
    assert retrospective.entries[0].memory.id == second.memory.id


def test_filters_and_output_order_are_deterministic() -> None:
    z_goal = memory(
        key="z.goal",
        memory_type=MemoryType.GOAL,
        version=1,
        state="ship",
        valid_from=T0,
        committed_at=T0,
    )
    a_fact = memory(
        key="a.fact",
        memory_type=MemoryType.FACT,
        version=1,
        state="verified",
        valid_from=T0,
        committed_at=T0,
    )
    ignored = memory(
        key="ignored.preference",
        memory_type=MemoryType.PREFERENCE,
        version=1,
        state="quiet",
        valid_from=T0,
        committed_at=T0,
    )

    full = project([z_goal, ignored, a_fact], as_of=T0)
    filtered = project(
        [z_goal, ignored, a_fact],
        as_of=T0,
        memory_keys=["z.goal", "a.fact"],
        memory_types=[MemoryType.GOAL, MemoryType.FACT],
    )

    assert [entry.memory.memory_key for entry in full.entries] == [
        "a.fact",
        "ignored.preference",
        "z.goal",
    ]
    assert [entry.memory.memory_key for entry in filtered.entries] == [
        "a.fact",
        "z.goal",
    ]
    assert filtered.filter_counts.filtered_by_key_or_type == 1


def test_naive_query_timestamps_are_rejected() -> None:
    with pytest.raises(ValidationError, match="as_of must be timezone-aware"):
        MemoryViewQuery(as_of=datetime(2026, 1, 1, 9, 0))

    with pytest.raises(ValidationError, match="known_at must be timezone-aware"):
        MemoryViewQuery(
            as_of=T0,
            known_at=datetime(2026, 1, 1, 9, 0),
        )


def test_naive_memory_validity_is_rejected_by_projection() -> None:
    naive = memory(
        version=1,
        state="planned",
        valid_from=datetime(2026, 1, 1, 9, 0),
        committed_at=T0,
    )

    with pytest.raises(InvalidMemoryTimestampError, match="memory.valid_from"):
        project([naive], as_of=T0)


def test_duplicate_key_version_history_is_ambiguous() -> None:
    first = memory(
        version=1,
        state="planned",
        valid_from=T0,
        committed_at=T0,
    )
    duplicate = memory(
        version=1,
        state="different",
        valid_from=T0,
        committed_at=T0,
        memory_id="mem_different_claim",
    )

    with pytest.raises(AmbiguousMemoryHistoryError, match="multiple committed nodes"):
        project([first, duplicate], as_of=T0)


def save_event(store: SQLiteExperienceStore, event_id: str) -> None:
    store.save(
        Event(
            id=event_id,
            actor_ids=["human_alexey"],
            summary=event_id,
            timestamp=T0,
            created_at=T0,
        )
    )


def test_sqlite_adapter_exposes_utc_knowledge_time_and_bitemporal_view() -> None:
    store = SQLiteExperienceStore()
    engine = ConsolidationEngine()
    save_event(store, "evt_v1")
    save_event(store, "evt_v2")

    first_result = engine.consolidate(
        MemoryMutation(
            memory_key="trip.singapore.status",
            memory_type=MemoryType.FACT,
            content={"state": "planned"},
            source_event_ids=["evt_v1"],
            confidence=0.9,
            importance=0.7,
            effective_at=T0,
        )
    )
    first = store.commit_memory(
        first_result,
        expected_head_id=None,
        expected_version=None,
    )
    second_result = engine.consolidate(
        MemoryMutation(
            memory_key=first.memory_key,
            memory_type=MemoryType.FACT,
            content={"state": "cancelled"},
            source_event_ids=["evt_v2"],
            confidence=0.95,
            importance=0.7,
            effective_at=T0 + timedelta(days=2),
        ),
        [first],
    )
    second = store.commit_memory(
        second_result,
        expected_head_id=first.id,
        expected_version=1,
    )

    with store._connect() as connection:
        connection.execute(
            "UPDATE records SET updated_at = ? WHERE id = ?",
            ("2026-01-02 09:00:00", first.id),
        )
        connection.execute(
            "UPDATE records SET updated_at = ? WHERE id = ?",
            ("2026-01-20 09:00:00", second.id),
        )

    adapter = SQLiteMemoryViewStore(store)
    committed = adapter.list_committed_memory_versions()
    historical = MemoryViewEngine(adapter).project(
        MemoryViewQuery(
            as_of=T0 + timedelta(days=10),
            known_at=T0 + timedelta(days=10),
        )
    )
    retrospective = MemoryViewEngine(adapter).project(
        MemoryViewQuery(as_of=T0 + timedelta(days=10))
    )

    assert [item.memory.id for item in committed] == [first.id, second.id]
    assert all(item.committed_at.tzinfo is UTC for item in committed)
    assert historical.entries[0].memory.id == first.id
    assert retrospective.entries[0].memory.id == second.id
