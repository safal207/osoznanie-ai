"""Protocol v0.1 domain models.

The models deliberately separate observations from interpretations and keep
provenance references explicit. They contain no LLM-specific code.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ValidationStatus(StrEnum):
    PROPOSED = "proposed"
    MACHINE_REVIEWED = "machine_reviewed"
    HUMAN_APPROVED = "human_approved"
    REJECTED = "rejected"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    EXPIRED = "expired"


class AccessPolicy(StrEnum):
    PRIVATE = "private"
    OWNER_AND_AGENT = "owner-and-agent"
    RELATIONSHIP = "relationship"
    TEAM = "team"
    ORGANIZATION = "organization"
    PUBLIC = "public"


class TrustLevel(StrEnum):
    UNTRUSTED = "untrusted"
    REPORTED = "reported"
    VERIFIED = "verified"


class OutcomeStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"
    UNKNOWN = "unknown"


class CommitmentStatus(StrEnum):
    OPEN = "open"
    COMPLETED = "completed"
    MISSED = "missed"
    CANCELLED = "cancelled"


class TraitStability(StrEnum):
    CORE = "core"
    ADAPTIVE = "adaptive"


class ProtocolRecord(BaseModel):
    """Common fields shared by every protocol record."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    id: str
    created_at: datetime = Field(default_factory=_utc_now)

    def reference_ids(self) -> tuple[str, ...]:
        """Return records that must already exist before this object is saved."""
        return ()


class Evidence(ProtocolRecord):
    id: str = Field(default_factory=lambda: _new_id("evd"))
    type: Literal["evidence"] = "evidence"
    source_type: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    content_hash: str | None = None
    captured_at: datetime = Field(default_factory=_utc_now)
    trust_level: TrustLevel = TrustLevel.REPORTED
    access_policy: AccessPolicy = AccessPolicy.OWNER_AND_AGENT
    owner_id: str | None = None
    agent_id: str | None = None
    tenant_id: str | None = None


class Event(ProtocolRecord):
    id: str = Field(default_factory=lambda: _new_id("evt"))
    type: Literal["event"] = "event"
    timestamp: datetime = Field(default_factory=_utc_now)
    actor_ids: list[str] = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)
    summary: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    sensitivity: str = "private"

    def reference_ids(self) -> tuple[str, ...]:
        return tuple(self.evidence_ids)


class Decision(ProtocolRecord):
    id: str = Field(default_factory=lambda: _new_id("dec"))
    type: Literal["decision"] = "decision"
    event_id: str
    agent_id: str
    chosen_action: str = Field(min_length=1)
    alternatives_considered: list[str] = Field(default_factory=list)
    reasoning_summary: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)

    def reference_ids(self) -> tuple[str, ...]:
        return (self.event_id, *self.evidence_ids)


class Outcome(ProtocolRecord):
    id: str = Field(default_factory=lambda: _new_id("out"))
    type: Literal["outcome"] = "outcome"
    decision_id: str
    status: OutcomeStatus
    summary: str = Field(min_length=1)
    impact: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    observed_at: datetime = Field(default_factory=_utc_now)

    def reference_ids(self) -> tuple[str, ...]:
        return (self.decision_id, *self.evidence_ids)


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)


class Reflection(ProtocolRecord):
    id: str = Field(default_factory=lambda: _new_id("ref"))
    type: Literal["reflection"] = "reflection"
    source_ids: list[str] = Field(min_length=1)
    hypotheses: list[Hypothesis] = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)
    validation_status: ValidationStatus = ValidationStatus.PROPOSED

    def reference_ids(self) -> tuple[str, ...]:
        hypothesis_evidence = [
            evidence_id
            for hypothesis in self.hypotheses
            for evidence_id in hypothesis.evidence_ids
        ]
        return tuple((*self.source_ids, *hypothesis_evidence))


class Lesson(ProtocolRecord):
    id: str = Field(default_factory=lambda: _new_id("les"))
    type: Literal["lesson"] = "lesson"
    statement: str = Field(min_length=1)
    scope: dict[str, Any] = Field(default_factory=dict)
    source_reflection_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    validation_status: ValidationStatus = ValidationStatus.PROPOSED
    effective_from: datetime = Field(default_factory=_utc_now)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_expiry(self) -> Lesson:
        if self.expires_at is not None and self.expires_at <= self.effective_from:
            raise ValueError("expires_at must be later than effective_from")
        return self

    def reference_ids(self) -> tuple[str, ...]:
        return tuple(self.source_reflection_ids)


class Commitment(ProtocolRecord):
    id: str = Field(default_factory=lambda: _new_id("com"))
    type: Literal["commitment"] = "commitment"
    agent_id: str
    counterparty_ids: list[str] = Field(min_length=1)
    statement: str = Field(min_length=1)
    created_from_ids: list[str] = Field(default_factory=list)
    due_at: datetime | None = None
    status: CommitmentStatus = CommitmentStatus.OPEN
    completion_evidence_ids: list[str] = Field(default_factory=list)

    def reference_ids(self) -> tuple[str, ...]:
        return tuple((*self.created_from_ids, *self.completion_evidence_ids))


class Trait(ProtocolRecord):
    id: str = Field(default_factory=lambda: _new_id("trt"))
    type: Literal["trait"] = "trait"
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    value: float = Field(ge=0.0, le=1.0)
    stability: TraitStability = TraitStability.ADAPTIVE
    source_lesson_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    validation_status: ValidationStatus = ValidationStatus.PROPOSED

    def reference_ids(self) -> tuple[str, ...]:
        return tuple(self.source_lesson_ids)


class IdentitySnapshot(ProtocolRecord):
    id: str = Field(default_factory=lambda: _new_id("ids"))
    type: Literal["identity_snapshot"] = "identity_snapshot"
    agent_id: str
    version: int = Field(ge=1)
    core_constraints: list[str] = Field(default_factory=list)
    active_trait_ids: list[str] = Field(default_factory=list)
    active_lesson_ids: list[str] = Field(default_factory=list)
    open_commitment_ids: list[str] = Field(default_factory=list)
    previous_snapshot_id: str | None = None
    change_summary: str = Field(min_length=1)
    approved_by: list[str] = Field(default_factory=list)

    def reference_ids(self) -> tuple[str, ...]:
        references: list[str] = [
            *self.active_trait_ids,
            *self.active_lesson_ids,
            *self.open_commitment_ids,
        ]
        if self.previous_snapshot_id is not None:
            references.append(self.previous_snapshot_id)
        return tuple(references)


Record = (
    Evidence
    | Event
    | Decision
    | Outcome
    | Reflection
    | Lesson
    | Commitment
    | Trait
    | IdentitySnapshot
)

RECORD_MODELS: dict[str, type[ProtocolRecord]] = {
    "evidence": Evidence,
    "event": Event,
    "decision": Decision,
    "outcome": Outcome,
    "reflection": Reflection,
    "lesson": Lesson,
    "commitment": Commitment,
    "trait": Trait,
    "identity_snapshot": IdentitySnapshot,
}
