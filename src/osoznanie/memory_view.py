"""Bitemporal, hard-gated state projection for committed memory histories."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .consolidation import AmbiguousMemoryHistoryError
from .memory import MemoryObject, MemoryStatus, MemoryType


class MemoryViewError(ValueError):
    """Base exception for invalid memory-state projection."""


class InvalidMemoryTimestampError(MemoryViewError):
    """Raised when a memory or query timestamp lacks an explicit timezone."""


def _require_aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _normalize_database_utc(value: datetime) -> datetime:
    """Normalize SQLite CURRENT_TIMESTAMP values, which are naive UTC in v0.1."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class CommittedMemoryVersion(BaseModel):
    """One committed memory node plus its repository knowledge-time timestamp."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    memory: MemoryObject
    committed_at: datetime

    @field_validator("committed_at")
    @classmethod
    def normalize_committed_at(cls, value: datetime) -> datetime:
        return _normalize_database_utc(value)


class MemoryViewQuery(BaseModel):
    """Request a deterministic view of governing memory state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    as_of: datetime
    known_at: datetime | None = None
    memory_keys: list[str] = Field(default_factory=list)
    memory_types: list[MemoryType] = Field(default_factory=list)

    @field_validator("as_of")
    @classmethod
    def normalize_as_of(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, field_name="as_of")

    @field_validator("known_at")
    @classmethod
    def normalize_known_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _require_aware_utc(value, field_name="known_at")

    @field_validator("memory_keys")
    @classmethod
    def normalize_memory_keys(cls, values: list[str]) -> list[str]:
        return sorted({value.strip() for value in values if value.strip()})

    @field_validator("memory_types")
    @classmethod
    def normalize_memory_types(cls, values: list[MemoryType]) -> list[MemoryType]:
        return sorted(set(values), key=lambda item: item.value)


class MemoryViewReasonCode(StrEnum):
    GOVERNING_VERSION = "governing_version"
    EFFECTIVE_AT_QUERY = "effective_at_query"
    ACTIVE_STATUS = "active_status"
    NOT_EXPIRED = "not_expired"
    KNOWN_BY_CUTOFF = "known_by_cutoff"


class MemoryViewGateReason(StrEnum):
    REVOKED = "revoked"
    DISPUTED = "disputed"
    OUTDATED = "outdated"
    EXPIRED = "expired"


class MemoryViewEntry(BaseModel):
    """One memory version admitted into the projected active state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    memory: MemoryObject
    committed_at: datetime
    reason_codes: list[MemoryViewReasonCode]


class MemoryViewRejection(BaseModel):
    """A governing version rejected by a lifecycle hard gate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_key: str
    memory_id: str
    version: int = Field(ge=1)
    reason: MemoryViewGateReason
    status: MemoryStatus


class MemoryViewFilterCounts(BaseModel):
    """Aggregate diagnostics from one projection pass."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    filtered_by_key_or_type: int = Field(default=0, ge=0)
    not_known_by_cutoff: int = Field(default=0, ge=0)
    not_yet_effective: int = Field(default=0, ge=0)
    superseded_versions: int = Field(default=0, ge=0)
    non_active_governing: int = Field(default=0, ge=0)
    expired_governing: int = Field(default=0, ge=0)


class MemoryView(BaseModel):
    """Deterministic active state plus explicit hard-gate diagnostics."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    as_of: datetime
    known_at: datetime | None
    entries: list[MemoryViewEntry]
    rejections: list[MemoryViewRejection]
    filter_counts: MemoryViewFilterCounts


class MemoryViewStore(Protocol):
    """Repository capability required by the state projector."""

    def list_committed_memory_versions(self) -> list[CommittedMemoryVersion]: ...


class MemoryViewEngine:
    """Project one governing, hard-gated version per logical memory key."""

    def __init__(self, store: MemoryViewStore) -> None:
        self.store = store

    def project(self, query: MemoryViewQuery) -> MemoryView:
        history = self.store.list_committed_memory_versions()
        self._validate_history(history)

        key_filter = set(query.memory_keys)
        type_filter = set(query.memory_types)
        grouped: dict[str, list[CommittedMemoryVersion]] = defaultdict(list)

        filtered_by_key_or_type = 0
        not_known_by_cutoff = 0
        not_yet_effective = 0

        for committed in history:
            memory = committed.memory
            self._validate_memory_timestamps(memory)

            if key_filter and memory.memory_key not in key_filter:
                filtered_by_key_or_type += 1
                continue
            if type_filter and memory.memory_type not in type_filter:
                filtered_by_key_or_type += 1
                continue
            if query.known_at is not None and committed.committed_at > query.known_at:
                not_known_by_cutoff += 1
                continue
            if memory.valid_from > query.as_of:
                not_yet_effective += 1
                continue
            grouped[memory.memory_key].append(committed)

        entries: list[MemoryViewEntry] = []
        rejections: list[MemoryViewRejection] = []
        superseded_versions = 0
        non_active_governing = 0
        expired_governing = 0

        for memory_key in sorted(grouped):
            candidates = grouped[memory_key]
            governing = max(
                candidates,
                key=lambda item: (
                    item.memory.version,
                    item.committed_at,
                    item.memory.id,
                ),
            )
            superseded_versions += len(candidates) - 1
            memory = governing.memory

            if memory.status is not MemoryStatus.ACTIVE:
                non_active_governing += 1
                rejections.append(
                    MemoryViewRejection(
                        memory_key=memory.memory_key,
                        memory_id=memory.id,
                        version=memory.version,
                        reason=MemoryViewGateReason(memory.status.value),
                        status=memory.status,
                    )
                )
                continue

            if memory.valid_until is not None and query.as_of >= memory.valid_until:
                expired_governing += 1
                rejections.append(
                    MemoryViewRejection(
                        memory_key=memory.memory_key,
                        memory_id=memory.id,
                        version=memory.version,
                        reason=MemoryViewGateReason.EXPIRED,
                        status=memory.status,
                    )
                )
                continue

            reason_codes = [
                MemoryViewReasonCode.GOVERNING_VERSION,
                MemoryViewReasonCode.EFFECTIVE_AT_QUERY,
                MemoryViewReasonCode.ACTIVE_STATUS,
                MemoryViewReasonCode.NOT_EXPIRED,
            ]
            if query.known_at is not None:
                reason_codes.append(MemoryViewReasonCode.KNOWN_BY_CUTOFF)

            entries.append(
                MemoryViewEntry(
                    memory=memory,
                    committed_at=governing.committed_at,
                    reason_codes=reason_codes,
                )
            )

        return MemoryView(
            as_of=query.as_of,
            known_at=query.known_at,
            entries=entries,
            rejections=rejections,
            filter_counts=MemoryViewFilterCounts(
                filtered_by_key_or_type=filtered_by_key_or_type,
                not_known_by_cutoff=not_known_by_cutoff,
                not_yet_effective=not_yet_effective,
                superseded_versions=superseded_versions,
                non_active_governing=non_active_governing,
                expired_governing=expired_governing,
            ),
        )

    @staticmethod
    def _validate_history(history: list[CommittedMemoryVersion]) -> None:
        seen: dict[tuple[str, int], str] = {}
        for committed in history:
            memory = committed.memory
            key = (memory.memory_key, memory.version)
            existing_id = seen.get(key)
            if existing_id is not None and existing_id != memory.id:
                raise AmbiguousMemoryHistoryError(
                    "multiple committed nodes claim "
                    f"{memory.memory_key} version {memory.version}: "
                    f"{existing_id}, {memory.id}"
                )
            seen[key] = memory.id

    @staticmethod
    def _validate_memory_timestamps(memory: MemoryObject) -> None:
        try:
            _require_aware_utc(memory.valid_from, field_name="memory.valid_from")
            if memory.valid_until is not None:
                _require_aware_utc(memory.valid_until, field_name="memory.valid_until")
        except ValueError as error:
            raise InvalidMemoryTimestampError(str(error)) from error
