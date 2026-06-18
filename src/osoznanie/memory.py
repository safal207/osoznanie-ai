"""Versioned, provenance-aware memory contracts for Osoznanie agents."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .models import ProtocolRecord, _new_id, _utc_now


class MemoryType(StrEnum):
    FACT = "fact"
    PREFERENCE = "preference"
    GOAL = "goal"
    CONSTRAINT = "constraint"
    INFERENCE = "inference"
    SKILL = "skill"
    FAILURE_PATTERN = "failure_pattern"
    BEHAVIORAL_RULE = "behavioral_rule"
    ACCESS_POLICY = "access_policy"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    OUTDATED = "outdated"
    DISPUTED = "disputed"
    REVOKED = "revoked"


def _normalize_reference_ids(values: list[str]) -> list[str]:
    return sorted({value.strip() for value in values if value.strip()})


class MemoryObject(ProtocolRecord):
    """One immutable version in a logical memory's auditable history."""

    id: str = Field(default_factory=lambda: _new_id("mem"))
    type: Literal["memory"] = "memory"
    memory_key: str = Field(min_length=1)
    memory_type: MemoryType
    content: dict[str, Any]
    source_event_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    importance: float = Field(ge=0.0, le=1.0)
    valid_from: datetime = Field(default_factory=_utc_now)
    valid_until: datetime | None = None
    status: MemoryStatus = MemoryStatus.ACTIVE
    supersedes: list[str] = Field(default_factory=list)
    contradicts: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=_utc_now)
    version: int = Field(default=1, ge=1)

    @field_validator("memory_key")
    @classmethod
    def normalize_memory_key(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("memory_key must not be blank")
        return normalized

    @field_validator("source_event_ids", "supersedes", "contradicts")
    @classmethod
    def normalize_references(cls, values: list[str]) -> list[str]:
        return _normalize_reference_ids(values)

    @model_validator(mode="after")
    def validate_memory_contract(self) -> MemoryObject:
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be later than valid_from")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at")
        if self.id in {*self.source_event_ids, *self.supersedes, *self.contradicts}:
            raise ValueError("a memory cannot reference itself")
        if set(self.supersedes) & set(self.contradicts):
            raise ValueError("a memory cannot both supersede and contradict the same record")
        if self.version == 1 and self.supersedes:
            raise ValueError("version 1 cannot supersede an earlier memory")
        if self.version > 1 and not self.supersedes:
            raise ValueError("versions after 1 must supersede at least one earlier memory")
        if (
            self.memory_type is MemoryType.FACT
            and self.status is MemoryStatus.ACTIVE
            and not self.source_event_ids
        ):
            raise ValueError("active factual memories require source_event_ids")
        return self

    def reference_ids(self) -> tuple[str, ...]:
        return tuple((*self.source_event_ids, *self.supersedes, *self.contradicts))

    def is_active_at(self, at: datetime | None = None) -> bool:
        """Return whether this version independently passes lifecycle hard gates."""
        moment = at or datetime.now(UTC)
        return (
            self.status is MemoryStatus.ACTIVE
            and self.valid_from <= moment
            and (self.valid_until is None or moment < self.valid_until)
        )

    def canonical_json(self) -> str:
        """Return deterministic JSON for hashing, signatures, and audit logs."""
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def resolve_active_memory(
    memories: Iterable[MemoryObject],
    *,
    memory_key: str,
    at: datetime | None = None,
) -> MemoryObject | None:
    """Resolve the governing version, then apply lifecycle hard gates.

    A revoked, disputed, outdated, or expired governing version blocks the key.
    Earlier active versions are never resurrected as a fallback.
    """
    moment = at or datetime.now(UTC)
    effective = [
        memory
        for memory in memories
        if memory.memory_key == memory_key and memory.valid_from <= moment
    ]
    if not effective:
        return None

    governing = max(
        effective,
        key=lambda memory: (memory.version, memory.updated_at, memory.id),
    )
    return governing if governing.is_active_at(moment) else None
