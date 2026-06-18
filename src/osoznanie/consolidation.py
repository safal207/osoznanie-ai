"""Deterministic consolidation of source-backed memory mutations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .memory import MemoryObject, MemoryStatus, MemoryType


class ConsolidationError(ValueError):
    """Base error for invalid or ambiguous consolidation requests."""


class MissingMemoryHistoryError(ConsolidationError):
    """Raised when an operation requires a previous memory version."""


class MemoryTypeChangeError(ConsolidationError):
    """Raised when a mutation tries to change a logical memory's type."""


class AmbiguousMemoryHistoryError(ConsolidationError):
    """Raised when history contains multiple records for the same version."""


class MemoryMutationKind(StrEnum):
    UPSERT = "upsert"
    DISPUTE = "dispute"
    REVOKE = "revoke"


def _normalize_ids(values: list[str]) -> list[str]:
    return sorted({value.strip() for value in values if value.strip()})


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


class MemoryMutation(BaseModel):
    """A normalized proposal that may be deterministically applied to memory history."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: MemoryMutationKind = MemoryMutationKind.UPSERT
    memory_key: str = Field(min_length=1)
    memory_type: MemoryType
    content: dict[str, Any]
    source_event_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    importance: float = Field(ge=0.0, le=1.0)
    effective_at: datetime
    valid_until: datetime | None = None
    contradicts: list[str] = Field(default_factory=list)

    @field_validator("memory_key")
    @classmethod
    def normalize_memory_key(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("memory_key must not be blank")
        return normalized

    @field_validator("source_event_ids", "contradicts")
    @classmethod
    def normalize_reference_ids(cls, values: list[str]) -> list[str]:
        normalized = _normalize_ids(values)
        if not normalized and cls.__name__ == "MemoryMutation":
            # Field(min_length=1) validates source_event_ids before this branch;
            # contradicts is allowed to remain empty.
            return normalized
        return normalized

    @field_validator("effective_at")
    @classmethod
    def normalize_effective_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)

    @field_validator("valid_until")
    @classmethod
    def normalize_valid_until(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _normalize_datetime(value)

    @model_validator(mode="after")
    def validate_temporal_window(self) -> MemoryMutation:
        if self.valid_until is not None and self.valid_until <= self.effective_at:
            raise ValueError("valid_until must be later than effective_at")
        if not self.source_event_ids:
            raise ValueError("source_event_ids must contain at least one event")
        return self

    def canonical_payload(self, *, previous: MemoryObject | None) -> dict[str, Any]:
        """Return the stable payload used to derive an idempotent memory id."""
        return {
            "kind": self.kind.value,
            "memory_key": self.memory_key,
            "memory_type": self.memory_type.value,
            "content": self.content,
            "source_event_ids": self.source_event_ids,
            "confidence": self.confidence,
            "importance": self.importance,
            "effective_at": self.effective_at.isoformat().replace("+00:00", "Z"),
            "valid_until": (
                None
                if self.valid_until is None
                else self.valid_until.isoformat().replace("+00:00", "Z")
            ),
            "contradicts": self.contradicts,
            "previous_memory_id": None if previous is None else previous.id,
            "previous_version": None if previous is None else previous.version,
        }


class ConsolidationResult(BaseModel):
    """Inspectable output of one deterministic consolidation operation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    memory: MemoryObject
    previous_memory_id: str | None
    mutation_kind: MemoryMutationKind
    change_summary: str = Field(min_length=1)


class ConsolidationEngine:
    """Create the next immutable memory version from normalized mutations."""

    def consolidate(
        self,
        mutation: MemoryMutation,
        history: Iterable[MemoryObject] = (),
    ) -> ConsolidationResult:
        relevant = [memory for memory in history if memory.memory_key == mutation.memory_key]
        previous = self._latest(relevant)

        if previous is None and mutation.kind is not MemoryMutationKind.UPSERT:
            raise MissingMemoryHistoryError(
                f"{mutation.kind.value} requires existing memory: {mutation.memory_key}"
            )
        if previous is not None and previous.memory_type is not mutation.memory_type:
            raise MemoryTypeChangeError(
                "memory_type cannot change across versions: "
                f"{previous.memory_type.value} -> {mutation.memory_type.value}"
            )

        version = 1 if previous is None else previous.version + 1
        status = {
            MemoryMutationKind.UPSERT: MemoryStatus.ACTIVE,
            MemoryMutationKind.DISPUTE: MemoryStatus.DISPUTED,
            MemoryMutationKind.REVOKE: MemoryStatus.REVOKED,
        }[mutation.kind]
        memory_id = self._memory_id(mutation, previous=previous)

        memory = MemoryObject(
            id=memory_id,
            memory_key=mutation.memory_key,
            memory_type=mutation.memory_type,
            content=mutation.content,
            source_event_ids=mutation.source_event_ids,
            confidence=mutation.confidence,
            importance=mutation.importance,
            valid_from=mutation.effective_at,
            valid_until=mutation.valid_until,
            status=status,
            supersedes=[] if previous is None else [previous.id],
            contradicts=mutation.contradicts,
            created_at=mutation.effective_at,
            updated_at=mutation.effective_at,
            version=version,
        )
        return ConsolidationResult(
            memory=memory,
            previous_memory_id=None if previous is None else previous.id,
            mutation_kind=mutation.kind,
            change_summary=self._change_summary(mutation, version=version),
        )

    @staticmethod
    def _latest(history: list[MemoryObject]) -> MemoryObject | None:
        by_version: dict[int, MemoryObject] = {}
        for memory in history:
            existing = by_version.get(memory.version)
            if existing is not None and existing.id != memory.id:
                raise AmbiguousMemoryHistoryError(
                    f"multiple memory records use version {memory.version}: "
                    f"{existing.id}, {memory.id}"
                )
            by_version[memory.version] = memory
        if not by_version:
            return None
        return by_version[max(by_version)]

    @staticmethod
    def _memory_id(mutation: MemoryMutation, *, previous: MemoryObject | None) -> str:
        canonical = json.dumps(
            mutation.canonical_payload(previous=previous),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"mem_{hashlib.sha256(canonical).hexdigest()[:32]}"

    @staticmethod
    def _change_summary(mutation: MemoryMutation, *, version: int) -> str:
        verb = {
            MemoryMutationKind.UPSERT: "Created" if version == 1 else "Updated",
            MemoryMutationKind.DISPUTE: "Disputed",
            MemoryMutationKind.REVOKE: "Revoked",
        }[mutation.kind]
        return f"{verb} {mutation.memory_key} as version {version}."
