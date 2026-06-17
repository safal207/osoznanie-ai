"""Extended report contracts for typed synthetic decision audits."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .claims import SyntheticClaim
from .models import RetrievedLessonSnapshot
from .simulation_models import (
    DecisionAggregateMetrics,
    DecisionTrialResult,
)


class AuditedDecisionTrialResult(DecisionTrialResult):
    """Decision result plus the exact typed retrieval output used by the policy."""

    returned_lessons: list[RetrievedLessonSnapshot] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_snapshot(self) -> AuditedDecisionTrialResult:
        snapshot_ids = [item.lesson_id for item in self.returned_lessons]
        snapshot_ranks = [item.rank for item in self.returned_lessons]
        if self.returned_lesson_count != len(self.returned_lessons):
            raise ValueError("returned count must match typed snapshot length")
        if self.returned_lesson_ids != snapshot_ids:
            raise ValueError("returned IDs must match typed snapshot order")
        if snapshot_ranks != list(range(1, len(snapshot_ranks) + 1)):
            raise ValueError("snapshot ranks must be contiguous from one")
        return self


class StructuredDecisionSimulationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    simulation_version: str
    evaluated_at: datetime
    claim: SyntheticClaim
    policy_name: str
    deterministic: bool
    trial_results: list[AuditedDecisionTrialResult]
    aggregates: list[DecisionAggregateMetrics]
