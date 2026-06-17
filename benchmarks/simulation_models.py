"""Contracts for deterministic decision-policy simulation."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import ErrorSignature, StrategyName


def _normalize(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError("value must not be blank")
    return normalized


def _normalized_list(values: list[str]) -> list[str]:
    return sorted({_normalize(value) for value in values})


def _normalized_context(values: dict[str, str]) -> dict[str, str]:
    return {
        _normalize(key): _normalize(value)
        for key, value in sorted(values.items())
    }


class DecisionDisposition(StrEnum):
    ACT = "act"
    ABSTAIN = "abstain"


class DecisionExplanationCode(StrEnum):
    NO_LESSON_DEFAULT = "no_lesson_default"
    TOP_ACTIONABLE_LESSON = "top_actionable_lesson"
    LESSON_NOT_ACTIONABLE = "lesson_not_actionable"


class ActionRecommendation(BaseModel):
    """Benchmark-only structured action recommendation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str
    applicable_task_types: list[str] = Field(default_factory=list)
    required_context: dict[str, str] = Field(default_factory=dict)

    @field_validator("action_id")
    @classmethod
    def normalize_action_id(cls, value: str) -> str:
        return _normalize(value)

    @field_validator("applicable_task_types")
    @classmethod
    def normalize_task_types(cls, values: list[str]) -> list[str]:
        return _normalized_list(values)

    @field_validator("required_context")
    @classmethod
    def normalize_context(cls, values: dict[str, str]) -> dict[str, str]:
        return _normalized_context(values)


class DecisionTask(BaseModel):
    """Runtime-visible task passed to a decision policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    domain: str
    task_type: str
    context: dict[str, str] = Field(default_factory=dict)
    available_actions: list[str] = Field(min_length=1)
    default_action_id: str

    @field_validator("task_id", "domain", "task_type", "default_action_id")
    @classmethod
    def normalize_fields(cls, value: str) -> str:
        return _normalize(value)

    @field_validator("context")
    @classmethod
    def normalize_context(cls, values: dict[str, str]) -> dict[str, str]:
        return _normalized_context(values)

    @field_validator("available_actions")
    @classmethod
    def normalize_actions(cls, values: list[str]) -> list[str]:
        return _normalized_list(values)

    @model_validator(mode="after")
    def validate_default_action(self) -> DecisionTask:
        if self.default_action_id not in self.available_actions:
            raise ValueError("default_action_id must be one of available_actions")
        return self


class PolicyLesson(BaseModel):
    """Lesson view exposed to a policy after retrieval metadata is removed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lesson_id: str
    statement: str
    rank: int = Field(ge=1)
    recommendation: ActionRecommendation | None = None

    @field_validator("lesson_id")
    @classmethod
    def normalize_lesson_id(cls, value: str) -> str:
        return _normalize(value)

    @field_validator("statement")
    @classmethod
    def validate_statement(cls, value: str) -> str:
        statement = value.strip()
        if not statement:
            raise ValueError("statement must not be blank")
        return statement


class PolicyInput(BaseModel):
    """Complete policy-visible input with no evaluator ground truth."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task: DecisionTask
    lessons: list[PolicyLesson] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_lesson_order(self) -> PolicyInput:
        ranks = [lesson.rank for lesson in self.lessons]
        lesson_ids = [lesson.lesson_id for lesson in self.lessons]
        if len(ranks) != len(set(ranks)):
            raise ValueError("lesson ranks must be unique")
        if len(lesson_ids) != len(set(lesson_ids)):
            raise ValueError("lesson IDs must be unique")
        if ranks != sorted(ranks):
            raise ValueError("lessons must be ordered by ascending rank")
        return self


class DecisionScenario(BaseModel):
    """Evaluator-only ground truth that must never be passed to a policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str
    task: DecisionTask
    error_signature: ErrorSignature
    safe_action_id: str
    repeated_error_action_id: str
    relevant_lesson_ids: list[str] = Field(min_length=1)
    recommendations: dict[str, ActionRecommendation]

    @field_validator("scenario_id", "safe_action_id", "repeated_error_action_id")
    @classmethod
    def normalize_fields(cls, value: str) -> str:
        return _normalize(value)

    @field_validator("relevant_lesson_ids")
    @classmethod
    def normalize_relevant_ids(cls, values: list[str]) -> list[str]:
        return _normalized_list(values)

    @field_validator("recommendations")
    @classmethod
    def normalize_recommendation_keys(
        cls,
        values: dict[str, ActionRecommendation],
    ) -> dict[str, ActionRecommendation]:
        return {_normalize(key): value for key, value in sorted(values.items())}

    @model_validator(mode="after")
    def validate_ground_truth(self) -> DecisionScenario:
        actions = set(self.task.available_actions)
        if self.safe_action_id not in actions:
            raise ValueError("safe_action_id must be one of task.available_actions")
        if self.repeated_error_action_id not in actions:
            raise ValueError(
                "repeated_error_action_id must be one of task.available_actions"
            )
        if self.safe_action_id == self.repeated_error_action_id:
            raise ValueError("safe and repeated-error actions must differ")
        missing = set(self.relevant_lesson_ids) - set(self.recommendations)
        if missing:
            raise ValueError(
                f"relevant lessons require recommendations: {sorted(missing)}"
            )
        return self


class SimulatedDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str | None = None
    disposition: DecisionDisposition
    applied_lesson_ids: list[str] = Field(default_factory=list)
    explanation_codes: list[DecisionExplanationCode] = Field(min_length=1)

    @field_validator("action_id")
    @classmethod
    def normalize_action_id(cls, value: str | None) -> str | None:
        return None if value is None else _normalize(value)

    @field_validator("applied_lesson_ids")
    @classmethod
    def normalize_lesson_ids(cls, values: list[str]) -> list[str]:
        return _normalized_list(values)

    @model_validator(mode="after")
    def validate_disposition(self) -> SimulatedDecision:
        if self.disposition is DecisionDisposition.ACT and self.action_id is None:
            raise ValueError("acting decisions require action_id")
        if self.disposition is DecisionDisposition.ABSTAIN and self.action_id is not None:
            raise ValueError("abstaining decisions must not include action_id")
        return self


class DecisionTrialResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    strategy: StrategyName
    returned_lesson_count: int = Field(ge=0)
    returned_lesson_ids: list[str] = Field(default_factory=list)
    decision: SimulatedDecision
    correct: bool
    repeated_error: bool
    lesson_applied: bool
    abstained: bool

    @field_validator("returned_lesson_ids")
    @classmethod
    def normalize_returned_lesson_ids(cls, values: list[str]) -> list[str]:
        normalized = [_normalize(value) for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("returned lesson IDs must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_returned_lesson_count(self) -> DecisionTrialResult:
        if self.returned_lesson_count != len(self.returned_lesson_ids):
            raise ValueError(
                "returned_lesson_count must match returned_lesson_ids length"
            )
        return self


class DecisionAggregateMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: StrategyName
    trial_count: int = Field(ge=1)
    correct_decision_rate: float = Field(ge=0.0, le=1.0)
    repeated_error_rate: float = Field(ge=0.0, le=1.0)
    lesson_application_rate: float = Field(ge=0.0, le=1.0)
    abstention_rate: float = Field(ge=0.0, le=1.0)
    policy_coverage: float = Field(ge=0.0, le=1.0)


class DecisionSimulationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    simulation_version: str
    evaluated_at: datetime
    claim: str
    policy_name: str
    deterministic: bool
    trial_results: list[DecisionTrialResult]
    aggregates: list[DecisionAggregateMetrics]
