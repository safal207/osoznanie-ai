"""Repository contracts for atomically committing memory graph heads."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from .consolidation import ConsolidationResult
from .memory import MemoryObject


class MemoryRepositoryError(RuntimeError):
    """Base exception for memory repository failures."""


class VersionConflictError(MemoryRepositoryError):
    """A normal compare-and-swap conflict caused by a stale writer."""

    def __init__(
        self,
        *,
        memory_key: str,
        expected_head_id: str | None,
        actual_head_id: str | None,
        expected_version: int | None,
        actual_version: int | None,
    ) -> None:
        self.memory_key = memory_key
        self.expected_head_id = expected_head_id
        self.actual_head_id = actual_head_id
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            "memory head changed for "
            f"{memory_key}: expected id/version "
            f"{expected_head_id!r}/{expected_version!r}, got "
            f"{actual_head_id!r}/{actual_version!r}"
        )


class InvalidMemoryProgressionError(MemoryRepositoryError):
    """A consolidation result does not advance exactly from the stored head."""


class UnsafeMemoryWriteError(MemoryRepositoryError):
    """A caller attempted to bypass atomic head management."""


class MemoryHead(BaseModel):
    """The explicit entry point for one logical memory's immutable version chain."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_key: str = Field(min_length=1)
    current_id: str = Field(min_length=1)
    current_version: int = Field(ge=1)
    updated_at: datetime


class MemoryRepository(Protocol):
    """Compare-and-swap persistence contract for memory graph heads."""

    def get_memory_head(self, memory_key: str) -> MemoryHead | None: ...

    def commit_memory(
        self,
        result: ConsolidationResult,
        *,
        expected_head_id: str | None,
        expected_version: int | None = None,
    ) -> MemoryObject: ...
