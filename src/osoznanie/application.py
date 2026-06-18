"""Typed contracts for lesson application, observation, and evaluation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import AccessPolicy, ProtocolRecord, _new_id, _utc_now

ScalarValue: TypeAlias = str | int | float | bool


class CriterionOperator(StrEnum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"


class CriterionResult(StrEnum):
    MET = "met"
    NOT_MET = "not_met"
    INDETERMINATE = "indeterminate"


class EvaluationReasonCode(StrEnum):
    MISSING_OBSERVATION = "missing_observation"
    LATE_OBSERVATION = "late_observation"
    ACCESS_DENIED = "access_denied"
    CONFLICTING_OBSERVATIONS = "conflicting_observations"
    INSUFFICIENT_OBSERVATIONS = "insufficient_observations"


def _normalize_ids(values: list[str]) -> list[str]:
    return sorted({value.strip() for value in values if value.strip()})


class CanonicalProtocolRecord(ProtocolRecord):
    """Immutable protocol record with deterministic UTF-8 JSON serialization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


class SuccessCriterion(CanonicalProtocolRecord):
    """A criterion fixed before retrieval and later applied to observations."""

    id: str = Field(default_factory=lambda: _new_id("crt"))
    type: Literal["success_criterion"] = "success_criterion"
    name: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    definition_version: str = Field(min_length=1)
    evaluator_type: str = Field(min_length=1)
    evaluator_version: str = Field(min_length=1)
    observation_window_seconds: int = Field(ge=0)
    metric_key: str = Field(min_length=1)
    operator: CriterionOperator
    expected_value: ScalarValue
    missing_observation_result: Literal["indeterminate"] = "indeterminate"
    fixed_at: datetime = Field(default_factory=_utc_now)
    access_policy: AccessPolicy = AccessPolicy.OWNER_AND_AGENT

    @field_validator(
        "name",
        "definition",
        "definition_version",
        "evaluator_type",
        "evaluator_version",
        "metric_key",
    )
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class LessonApplication(CanonicalProtocolRecord):
    """Evidence that one lesson was actually used for one action execution."""

    id: str = Field(default_factory=lambda: _new_id("app"))
    type: Literal["lesson_application"] = "lesson_application"
    lesson_id: str = Field(min_length=1)
    recall_query_id: str = Field(min_length=1)
    retrieval_execution_id: str = Field(min_length=1)
    action_execution_id: str = Field(min_length=1)
    success_criterion_id: str = Field(min_length=1)
    environment_snapshot_id: str = Field(min_length=1)
    environment_projection_id: str | None = None
    actor_id: str | None = None
    applied_at: datetime = Field(default_factory=_utc_now)
    idempotency_key: str = Field(min_length=1)
    access_policy: AccessPolicy = AccessPolicy.OWNER_AND_AGENT

    @field_validator(
        "lesson_id",
        "recall_query_id",
        "retrieval_execution_id",
        "action_execution_id",
        "success_criterion_id",
        "environment_snapshot_id",
        "environment_projection_id",
        "actor_id",
        "idempotency_key",
    )
    @classmethod
    def normalize_reference_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    def reference_ids(self) -> tuple[str, ...]:
        references = [
            self.lesson_id,
            self.recall_query_id,
            self.retrieval_execution_id,
            self.action_execution_id,
            self.success_criterion_id,
            self.environment_snapshot_id,
        ]
        if self.environment_projection_id is not None:
            references.append(self.environment_projection_id)
        return tuple(references)

    def idempotency_fingerprint(self) -> str:
        """Hash semantic payload while excluding delivery-generated identity fields."""

        payload = self.model_dump(mode="json", exclude={"id", "created_at"})
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class ObservationValue(BaseModel):
    """One typed measurement in an outcome observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(min_length=1)
    value: ScalarValue
    unit: str | None = None

    @field_validator("key", "unit")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class OutcomeObservation(CanonicalProtocolRecord):
    """Append-only measured facts observed after lesson application."""

    id: str = Field(default_factory=lambda: _new_id("obs"))
    type: Literal["outcome_observation"] = "outcome_observation"
    lesson_application_ids: list[str] = Field(min_length=1)
    action_execution_id: str = Field(min_length=1)
    observed_at: datetime = Field(default_factory=_utc_now)
    values: list[ObservationValue] = Field(min_length=1)
    source_event_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    collection_policy_version: str = Field(min_length=1)
    access_policy: AccessPolicy = AccessPolicy.OWNER_AND_AGENT
    supersedes_observation_id: str | None = None

    @field_validator("lesson_application_ids", "source_event_ids", "evidence_ids")
    @classmethod
    def normalize_references(cls, values: list[str]) -> list[str]:
        return _normalize_ids(values)

    @field_validator(
        "action_execution_id",
        "collection_policy_version",
        "supersedes_observation_id",
    )
    @classmethod
    def normalize_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("values")
    @classmethod
    def normalize_values(cls, values: list[ObservationValue]) -> list[ObservationValue]:
        ordered = sorted(values, key=lambda item: item.key)
        keys = [item.key for item in ordered]
        if len(keys) != len(set(keys)):
            raise ValueError("observation value keys must be unique")
        return ordered

    def reference_ids(self) -> tuple[str, ...]:
        references = [
            *self.lesson_application_ids,
            self.action_execution_id,
            *self.source_event_ids,
            *self.evidence_ids,
        ]
        if self.supersedes_observation_id is not None:
            references.append(self.supersedes_observation_id)
        return tuple(references)


class CriterionEvaluation(CanonicalProtocolRecord):
    """Reproducible evaluation of a fixed criterion against observations."""

    id: str = Field(default_factory=lambda: _new_id("cev"))
    type: Literal["criterion_evaluation"] = "criterion_evaluation"
    criterion_id: str = Field(min_length=1)
    lesson_application_ids: list[str] = Field(min_length=1)
    observation_ids: list[str] = Field(default_factory=list)
    result: CriterionResult
    reason_codes: list[EvaluationReasonCode] = Field(default_factory=list)
    evaluator_version: str = Field(min_length=1)
    evaluated_at: datetime = Field(default_factory=_utc_now)
    supersedes_evaluation_id: str | None = None
    access_policy: AccessPolicy = AccessPolicy.OWNER_AND_AGENT

    @field_validator("lesson_application_ids", "observation_ids")
    @classmethod
    def normalize_references(cls, values: list[str]) -> list[str]:
        return _normalize_ids(values)

    @field_validator("reason_codes")
    @classmethod
    def normalize_reason_codes(
        cls,
        values: list[EvaluationReasonCode],
    ) -> list[EvaluationReasonCode]:
        return sorted(set(values), key=lambda item: item.value)

    @field_validator("criterion_id", "evaluator_version", "supersedes_evaluation_id")
    @classmethod
    def normalize_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_result_contract(self) -> CriterionEvaluation:
        if self.result is CriterionResult.INDETERMINATE:
            if not self.reason_codes:
                raise ValueError("indeterminate evaluation requires reason codes")
        else:
            if not self.observation_ids:
                raise ValueError("determinate evaluation requires observations")
            if self.reason_codes:
                raise ValueError("determinate evaluation must not contain reason codes")
        return self

    def reference_ids(self) -> tuple[str, ...]:
        references = [
            self.criterion_id,
            *self.lesson_application_ids,
            *self.observation_ids,
        ]
        if self.supersedes_evaluation_id is not None:
            references.append(self.supersedes_evaluation_id)
        return tuple(references)


ApplicationRecord: TypeAlias = (
    SuccessCriterion
    | LessonApplication
    | OutcomeObservation
    | CriterionEvaluation
)

APPLICATION_RECORD_MODELS: dict[str, type[CanonicalProtocolRecord]] = {
    "success_criterion": SuccessCriterion,
    "lesson_application": LessonApplication,
    "outcome_observation": OutcomeObservation,
    "criterion_evaluation": CriterionEvaluation,
}
