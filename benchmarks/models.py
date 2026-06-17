"""Benchmark-only contracts and report models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from osoznanie.recall import (
    ProvenanceRef,
    ReasonCode,
    RecallFilterCounts,
    RecallQuery,
    ScoreBreakdown,
)

from .claims import SyntheticClaim


def _normalize(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError("value must not be blank")
    return normalized


def _normalized_ids(values: list[str]) -> list[str]:
    normalized = {_normalize(value) for value in values}
    return sorted(normalized)


class StrategyName(StrEnum):
    NO_MEMORY = "no_memory"
    NAIVE_KEYWORD = "naive_keyword"
    OSOZNANIE_RECALL = "osoznanie_recall"


class ErrorSignature(BaseModel):
    """Exact benchmark identity for a repeated-error class."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    domain: str
    task_type: str
    pattern_id: str
    version: int = Field(ge=1)

    @field_validator("domain", "task_type", "pattern_id")
    @classmethod
    def normalize_fields(cls, value: str) -> str:
        return _normalize(value)

    @property
    def key(self) -> tuple[str, str, str, int]:
        return self.domain, self.task_type, self.pattern_id, self.version

    def matches(self, other: ErrorSignature) -> bool:
        return self.key == other.key


class RetrievalBenchmarkScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    description: str
    query: RecallQuery
    error_signature: ErrorSignature
    relevant_lesson_ids: list[str] = Field(min_length=1)
    decoy_lesson_ids: list[str] = Field(default_factory=list)

    @field_validator("scenario_id")
    @classmethod
    def normalize_scenario_id(cls, value: str) -> str:
        return _normalize(value)

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        description = value.strip()
        if not description:
            raise ValueError("description must not be blank")
        return description

    @field_validator("relevant_lesson_ids", "decoy_lesson_ids")
    @classmethod
    def normalize_ids(cls, values: list[str]) -> list[str]:
        return _normalized_ids(values)

    @model_validator(mode="after")
    def validate_ground_truth(self) -> RetrievalBenchmarkScenario:
        overlap = set(self.relevant_lesson_ids) & set(self.decoy_lesson_ids)
        if overlap:
            raise ValueError(
                f"lesson IDs cannot be both relevant and decoys: {sorted(overlap)}"
            )
        return self


class RankedLesson(BaseModel):
    """Public retrieval result used in benchmark reports."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lesson_id: str
    score: float = Field(ge=0.0, le=1.0)
    rank: int = Field(ge=1)

    @field_validator("lesson_id")
    @classmethod
    def normalize_lesson_id(cls, value: str) -> str:
        return _normalize(value)


class RetrievedLessonSnapshot(RankedLesson):
    """Exact typed retrieval snapshot retained for restricted audit output."""

    score_breakdown: ScoreBreakdown | None = None
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    provenance_refs: list[ProvenanceRef] = Field(default_factory=list)

    def public_view(self) -> RankedLesson:
        return RankedLesson(
            lesson_id=self.lesson_id,
            score=self.score,
            rank=self.rank,
        )


class RetrievalExecution(BaseModel):
    """Typed benchmark retrieval output plus optional structured diagnostics."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lessons: list[RetrievedLessonSnapshot] = Field(default_factory=list)
    filter_counts: RecallFilterCounts | None = None

    @model_validator(mode="after")
    def validate_lesson_order(self) -> RetrievalExecution:
        ranks = [item.rank for item in self.lessons]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("retrieval ranks must be contiguous from one")
        lesson_ids = [item.lesson_id for item in self.lessons]
        if len(lesson_ids) != len(set(lesson_ids)):
            raise ValueError("retrieval lesson IDs must be unique")
        return self


class ScenarioMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    strategy: StrategyName
    returned_count: int = Field(ge=0)
    relevant_rank: int | None = Field(default=None, ge=1)
    hit_at_1: bool
    hit_at_3: bool
    reciprocal_rank: float = Field(ge=0.0, le=1.0)
    false_discovery_rate: float = Field(ge=0.0, le=1.0)
    decoy_selection_rate: float = Field(ge=0.0, le=1.0)
    returned_score_gap: float | None = Field(default=None, ge=-1.0, le=1.0)
    ranked_lessons: list[RankedLesson]


class AggregateMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: StrategyName
    scenario_count: int = Field(ge=1)
    hit_rate_at_1: float = Field(ge=0.0, le=1.0)
    hit_rate_at_3: float = Field(ge=0.0, le=1.0)
    mean_reciprocal_rank: float = Field(ge=0.0, le=1.0)
    mean_false_discovery_rate: float = Field(ge=0.0, le=1.0)
    mean_decoy_selection_rate: float = Field(ge=0.0, le=1.0)
    mean_returned_score_gap: float | None = Field(default=None, ge=-1.0, le=1.0)


class BenchmarkReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    benchmark_version: str
    evaluated_at: datetime
    claim: SyntheticClaim
    scenario_results: list[ScenarioMetrics]
    aggregates: list[AggregateMetrics]
