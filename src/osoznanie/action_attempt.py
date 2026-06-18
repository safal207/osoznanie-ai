"""Immutable evidence records for external action dispatch attempts."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from .action_outbox import ActionIntent, ActionIntentStatus
from .models import ProtocolRecord


class ActionAttemptError(RuntimeError):
    """Base exception for invalid action-attempt evidence."""


class ActionAttemptContractError(ActionAttemptError):
    """Attempt metadata does not match the leased action intent."""


class ActionAttemptStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def _aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _normalize_required(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def hash_lease_token(lease_token: str) -> str:
    """Return a stable non-reversible digest; never persist the raw lease token."""
    normalized = _normalize_required(lease_token, field_name="lease_token")
    return f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


class ActionAttempt(ProtocolRecord):
    """One immutable revision in the evidence chain for a real tool invocation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    type: Literal["action_attempt"] = "action_attempt"
    intent_id: str = Field(min_length=1)
    attempt_number: int = Field(ge=1)
    revision: int = Field(ge=1, le=2)
    supersedes_attempt_id: str | None = None
    worker_id: str = Field(min_length=1)
    lease_token_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    tool_name: str = Field(min_length=1)
    tool_call_id: str | None = None
    idempotency_key: str = Field(min_length=1)
    input_hash: str | None = None
    status: ActionAttemptStatus
    started_at: datetime
    finished_at: datetime | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    response_hash: str | None = None
    error_code: str | None = None
    outcome_id: str | None = None

    @field_validator(
        "id",
        "intent_id",
        "worker_id",
        "lease_token_hash",
        "tool_name",
        "idempotency_key",
    )
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _normalize_required(value, field_name="action-attempt field")

    @field_validator(
        "supersedes_attempt_id",
        "tool_call_id",
        "input_hash",
        "response_hash",
        "error_code",
        "outcome_id",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return _normalize_optional(value)

    @field_validator("started_at")
    @classmethod
    def normalize_started_at(cls, value: datetime) -> datetime:
        return _aware_utc(value, field_name="started_at")

    @field_validator("finished_at")
    @classmethod
    def normalize_finished_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _aware_utc(value, field_name="finished_at")

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        return _aware_utc(value, field_name="created_at")

    @model_validator(mode="after")
    def validate_contract(self) -> ActionAttempt:
        if self.supersedes_attempt_id == self.id:
            raise ValueError("an action attempt cannot supersede itself")

        terminal_fields = (
            self.finished_at,
            self.latency_ms,
            self.response_hash,
            self.error_code,
            self.outcome_id,
        )
        if self.revision == 1:
            if self.status is not ActionAttemptStatus.STARTED:
                raise ValueError("revision 1 action attempts must be started")
            if self.supersedes_attempt_id is not None:
                raise ValueError("revision 1 action attempts cannot supersede another record")
            if any(value is not None for value in terminal_fields):
                raise ValueError("started action attempts cannot contain terminal fields")
            if self.created_at != self.started_at:
                raise ValueError("started action attempts must be created at started_at")
            return self

        if self.status is ActionAttemptStatus.STARTED:
            raise ValueError("revision 2 action attempts must be terminal")
        if self.supersedes_attempt_id is None:
            raise ValueError("revision 2 action attempts must supersede the started record")
        if self.finished_at is None or self.latency_ms is None:
            raise ValueError("terminal action attempts require finished_at and latency_ms")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not be earlier than started_at")
        expected_latency = int(
            (self.finished_at - self.started_at).total_seconds() * 1000
        )
        if self.latency_ms != expected_latency:
            raise ValueError("latency_ms must match started_at and finished_at")
        if self.created_at != self.finished_at:
            raise ValueError("terminal action attempts must be created at finished_at")

        if self.status is ActionAttemptStatus.SUCCEEDED:
            if self.outcome_id is None:
                raise ValueError("succeeded action attempts require outcome_id")
            if self.error_code is not None:
                raise ValueError("succeeded action attempts cannot contain error_code")
        elif self.status is ActionAttemptStatus.FAILED:
            if self.error_code is None:
                raise ValueError("failed action attempts require error_code")
            if self.outcome_id is not None:
                raise ValueError("failed action attempts cannot contain outcome_id")
        return self

    def reference_ids(self) -> tuple[str, ...]:
        references: list[str] = []
        if self.supersedes_attempt_id is not None:
            references.append(self.supersedes_attempt_id)
        if self.outcome_id is not None:
            references.append(self.outcome_id)
        return tuple(references)

    def canonical_payload(self) -> dict[str, object]:
        payload = self.model_dump(mode="json")
        payload.pop("id")
        return payload

    @staticmethod
    def derive_id(payload: dict[str, object]) -> str:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"atm_{hashlib.sha256(canonical).hexdigest()[:32]}"


def build_started_attempt(
    intent: ActionIntent,
    worker_id: str,
    lease_token: str,
    started_at: datetime,
) -> ActionAttempt:
    """Create immutable pre-dispatch evidence from the current live lease."""
    started = _aware_utc(started_at, field_name="started_at")
    worker = _normalize_required(worker_id, field_name="worker_id")
    token = _normalize_required(lease_token, field_name="lease_token")
    if intent.status is not ActionIntentStatus.LEASED:
        raise ActionAttemptContractError("starting an attempt requires a leased intent")
    if intent.lease_owner != worker or intent.lease_token != token:
        raise ActionAttemptContractError("worker and lease token must match the leased intent")
    if started < intent.updated_at:
        raise ActionAttemptContractError("started_at cannot be earlier than the lease claim")
    if intent.lease_expires_at is None or intent.lease_expires_at <= started:
        raise ActionAttemptContractError("cannot start an attempt with an expired lease")
    if intent.attempt_count < 1:
        raise ActionAttemptContractError("leased intents require a positive attempt_count")

    values: dict[str, object] = {
        "intent_id": intent.id,
        "attempt_number": intent.attempt_count,
        "revision": 1,
        "supersedes_attempt_id": None,
        "worker_id": worker,
        "lease_token_hash": hash_lease_token(token),
        "tool_name": intent.tool_name,
        "tool_call_id": intent.tool_call_id,
        "idempotency_key": intent.idempotency_key,
        "input_hash": intent.input_hash,
        "status": ActionAttemptStatus.STARTED,
        "started_at": started,
        "finished_at": None,
        "latency_ms": None,
        "response_hash": None,
        "error_code": None,
        "outcome_id": None,
        "created_at": started,
    }
    payload = ActionAttempt(id="pending", **values).canonical_payload()
    return ActionAttempt(id=ActionAttempt.derive_id(payload), **values)


def build_succeeded_attempt(
    started_attempt: ActionAttempt,
    finished_at: datetime,
    outcome_id: str,
    *,
    response_hash: str | None = None,
) -> ActionAttempt:
    """Append immutable success evidence without modifying the started record."""
    return _build_terminal_attempt(
        started_attempt,
        status=ActionAttemptStatus.SUCCEEDED,
        finished_at=finished_at,
        outcome_id=_normalize_required(outcome_id, field_name="outcome_id"),
        response_hash=response_hash,
    )


def build_failed_attempt(
    started_attempt: ActionAttempt,
    finished_at: datetime,
    error_code: str,
    *,
    response_hash: str | None = None,
) -> ActionAttempt:
    """Append immutable failure evidence without modifying the started record."""
    return _build_terminal_attempt(
        started_attempt,
        status=ActionAttemptStatus.FAILED,
        finished_at=finished_at,
        error_code=_normalize_required(error_code, field_name="error_code"),
        response_hash=response_hash,
    )


def _build_terminal_attempt(
    started_attempt: ActionAttempt,
    *,
    status: ActionAttemptStatus,
    finished_at: datetime,
    response_hash: str | None,
    error_code: str | None = None,
    outcome_id: str | None = None,
) -> ActionAttempt:
    if (
        started_attempt.revision != 1
        or started_attempt.status is not ActionAttemptStatus.STARTED
    ):
        raise ActionAttemptContractError("terminal evidence requires a started attempt")
    finished = _aware_utc(finished_at, field_name="finished_at")
    if finished < started_attempt.started_at:
        raise ActionAttemptContractError("finished_at must not be earlier than started_at")
    latency_ms = int((finished - started_attempt.started_at).total_seconds() * 1000)
    values: dict[str, object] = {
        "intent_id": started_attempt.intent_id,
        "attempt_number": started_attempt.attempt_number,
        "revision": 2,
        "supersedes_attempt_id": started_attempt.id,
        "worker_id": started_attempt.worker_id,
        "lease_token_hash": started_attempt.lease_token_hash,
        "tool_name": started_attempt.tool_name,
        "tool_call_id": started_attempt.tool_call_id,
        "idempotency_key": started_attempt.idempotency_key,
        "input_hash": started_attempt.input_hash,
        "status": status,
        "started_at": started_attempt.started_at,
        "finished_at": finished,
        "latency_ms": latency_ms,
        "response_hash": _normalize_optional(response_hash),
        "error_code": error_code,
        "outcome_id": outcome_id,
        "created_at": finished,
    }
    payload = ActionAttempt(id="pending", **values).canonical_payload()
    return ActionAttempt(id=ActionAttempt.derive_id(payload), **values)
