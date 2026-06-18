"""Immutable, deterministic audit records for agent decisions and outcomes."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from .models import ProtocolRecord


class TraceAuthorizationDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


def _normalize_ids(values: list[str]) -> list[str]:
    return sorted({value.strip() for value in values if value.strip()})


def _normalize_texts(values: list[str]) -> list[str]:
    return sorted({value.strip() for value in values if value.strip()})


def _aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


class DecisionTrace(ProtocolRecord):
    """Captured evidence of the exact context used for one agent decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    type: Literal["decision_trace"] = "decision_trace"
    requester_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    authorization_decision: TraceAuthorizationDecision
    policy_memory_ids: list[str] = Field(default_factory=list)
    memory_ids: list[str] = Field(default_factory=list)
    as_of: datetime
    known_at: datetime | None = None
    decision_at: datetime
    alternatives_considered: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    tool_name: str | None = None
    tool_call_id: str | None = None
    input_hash: str | None = None
    outcome_id: str | None = None
    supersedes_trace_id: str | None = None
    trace_version: int = Field(default=1, ge=1)

    @field_validator("requester_id", "agent_id", "action")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("required trace text must not be blank")
        return normalized

    @field_validator("tool_name", "tool_call_id", "input_hash", "outcome_id", "supersedes_trace_id")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("policy_memory_ids", "memory_ids")
    @classmethod
    def normalize_reference_ids(cls, values: list[str]) -> list[str]:
        return _normalize_ids(values)

    @field_validator("alternatives_considered", "reason_codes")
    @classmethod
    def normalize_lists(cls, values: list[str]) -> list[str]:
        return _normalize_texts(values)

    @field_validator("as_of")
    @classmethod
    def normalize_as_of(cls, value: datetime) -> datetime:
        return _aware_utc(value, field_name="as_of")

    @field_validator("known_at")
    @classmethod
    def normalize_known_at(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware_utc(value, field_name="known_at")

    @field_validator("decision_at")
    @classmethod
    def normalize_decision_at(cls, value: datetime) -> datetime:
        return _aware_utc(value, field_name="decision_at")

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        return _aware_utc(value, field_name="created_at")

    @model_validator(mode="after")
    def validate_trace_contract(self) -> DecisionTrace:
        if self.known_at is not None and self.known_at > self.decision_at:
            raise ValueError("known_at must not be later than decision_at")
        if self.decision_at < self.as_of:
            raise ValueError("decision_at must not be earlier than as_of")
        if (
            self.authorization_decision is TraceAuthorizationDecision.ALLOW
            and not self.policy_memory_ids
        ):
            raise ValueError("allow traces require at least one policy memory id")
        if self.tool_call_id is not None and self.tool_name is None:
            raise ValueError("tool_call_id requires tool_name")
        if self.trace_version == 1 and self.supersedes_trace_id is not None:
            raise ValueError("trace version 1 cannot supersede another trace")
        if self.trace_version > 1 and self.supersedes_trace_id is None:
            raise ValueError("trace versions after 1 must supersede an earlier trace")
        if self.supersedes_trace_id == self.id:
            raise ValueError("a decision trace cannot supersede itself")
        references = set(self.reference_ids())
        if self.id in references:
            raise ValueError("a decision trace cannot reference itself")
        return self

    def reference_ids(self) -> tuple[str, ...]:
        references = [*self.policy_memory_ids, *self.memory_ids]
        if self.outcome_id is not None:
            references.append(self.outcome_id)
        if self.supersedes_trace_id is not None:
            references.append(self.supersedes_trace_id)
        return tuple(references)

    def canonical_payload(self) -> dict[str, object]:
        payload = self.model_dump(mode="json")
        payload.pop("id")
        return payload

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def derive_id(payload: dict[str, object]) -> str:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"dtr_{hashlib.sha256(canonical).hexdigest()[:32]}"
