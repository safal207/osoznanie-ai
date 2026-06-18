"""Deterministic action intents for durable audited dispatch."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import ConfigDict, Field, field_validator, model_validator

from .decision_trace import DecisionTrace, TraceAuthorizationDecision
from .models import ProtocolRecord


class ActionOutboxError(RuntimeError):
    """Base exception for transactional action dispatch."""


class ActionIntentContractError(ActionOutboxError):
    """Trace and dispatch metadata do not describe the same action."""


class OutboxIdempotencyConflictError(ActionOutboxError):
    """A deterministic action-intent identity has conflicting content."""


class ActionIntentNotFoundError(ActionOutboxError):
    """The requested action intent does not exist."""


class LeaseConflictError(ActionOutboxError):
    """A worker attempted to use a missing, stale, or expired lease."""


class TerminalActionIntentError(ActionOutboxError):
    """A completed or permanently failed action intent cannot transition."""


class ActionIntentStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    COMPLETED = "completed"
    FAILED = "failed"


class ActionIntentProposal(Protocol):
    action: str
    tool_name: str | None
    tool_call_id: str | None
    input_hash: str | None


def aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


class ActionIntent(ProtocolRecord):
    """Safe durable dispatch metadata; raw protected payloads are never stored."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    type: Literal["action_intent"] = "action_intent"
    trace_id: str = Field(min_length=1)
    requester_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    tool_call_id: str | None = None
    input_hash: str | None = None
    idempotency_key: str = Field(min_length=1)
    status: ActionIntentStatus = ActionIntentStatus.PENDING
    attempt_count: int = Field(default=0, ge=0)
    available_at: datetime
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_expires_at: datetime | None = None
    last_error_code: str | None = None
    outcome_id: str | None = None
    updated_at: datetime

    @field_validator(
        "id",
        "trace_id",
        "requester_id",
        "agent_id",
        "action",
        "tool_name",
        "idempotency_key",
    )
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("required action-intent text must not be blank")
        return normalized

    @field_validator(
        "tool_call_id",
        "input_hash",
        "lease_owner",
        "lease_token",
        "last_error_code",
        "outcome_id",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return normalize_optional(value)

    @field_validator("available_at")
    @classmethod
    def normalize_available_at(cls, value: datetime) -> datetime:
        return aware_utc(value, field_name="available_at")

    @field_validator("lease_expires_at")
    @classmethod
    def normalize_lease_expires_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return aware_utc(value, field_name="lease_expires_at")

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        return aware_utc(value, field_name="created_at")

    @field_validator("updated_at")
    @classmethod
    def normalize_updated_at(cls, value: datetime) -> datetime:
        return aware_utc(value, field_name="updated_at")

    @model_validator(mode="after")
    def validate_state(self) -> ActionIntent:
        lease_fields = (
            self.lease_owner,
            self.lease_token,
            self.lease_expires_at,
        )
        if self.status is ActionIntentStatus.LEASED:
            if any(value is None for value in lease_fields):
                raise ValueError("leased action intents require complete lease metadata")
            if self.outcome_id is not None:
                raise ValueError("leased action intents cannot have an outcome")
        elif any(value is not None for value in lease_fields):
            raise ValueError("only leased action intents may contain lease metadata")

        if self.status is ActionIntentStatus.COMPLETED:
            if self.outcome_id is None:
                raise ValueError("completed action intents require outcome_id")
        elif self.outcome_id is not None:
            raise ValueError("only completed action intents may contain outcome_id")

        if self.status is ActionIntentStatus.FAILED and self.last_error_code is None:
            raise ValueError("failed action intents require last_error_code")
        if self.available_at < self.created_at:
            raise ValueError("available_at must not be earlier than created_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at")
        return self

    def reference_ids(self) -> tuple[str, ...]:
        references = [self.trace_id]
        if self.outcome_id is not None:
            references.append(self.outcome_id)
        return tuple(references)

    def immutable_payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "trace_id": self.trace_id,
            "requester_id": self.requester_id,
            "agent_id": self.agent_id,
            "action": self.action,
            "tool_name": self.tool_name,
            "tool_call_id": self.tool_call_id,
            "input_hash": self.input_hash,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at.isoformat(),
        }


def build_action_intent(
    trace: DecisionTrace,
    proposal: ActionIntentProposal,
    *,
    available_at: datetime | None = None,
) -> ActionIntent:
    """Build one deterministic intent from the exact persisted decision context."""
    if trace.authorization_decision is not TraceAuthorizationDecision.ALLOW:
        raise ActionIntentContractError("outbox dispatch requires an allow trace")
    action = proposal.action.strip()
    tool_name = normalize_optional(proposal.tool_name)
    tool_call_id = normalize_optional(proposal.tool_call_id)
    input_hash = normalize_optional(proposal.input_hash)
    if tool_name is None:
        raise ActionIntentContractError("outbox dispatch requires tool_name")
    if (
        action != trace.action
        or tool_name != trace.tool_name
        or tool_call_id != trace.tool_call_id
        or input_hash != trace.input_hash
    ):
        raise ActionIntentContractError(
            "proposal dispatch metadata must match the captured decision trace"
        )

    identity_payload = {
        "trace_id": trace.id,
        "requester_id": trace.requester_id,
        "agent_id": trace.agent_id,
        "action": action,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "input_hash": input_hash,
    }
    canonical = json.dumps(
        identity_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    initial_time = trace.created_at
    ready_at = aware_utc(
        available_at or trace.decision_at,
        field_name="available_at",
    )
    return ActionIntent(
        id=f"act_{digest[:32]}",
        trace_id=trace.id,
        requester_id=trace.requester_id,
        agent_id=trace.agent_id,
        action=action,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        input_hash=input_hash,
        idempotency_key=f"osi_{digest}",
        available_at=ready_at,
        created_at=initial_time,
        updated_at=initial_time,
    )
