"""Extended report contracts for typed synthetic decision audits."""

from __future__ import annotations

from pydantic import Field, model_validator

from osoznanie.recall import RecallFilterCounts

from .claims import SyntheticClaim
from .models import RetrievedLessonSnapshot, StrategyName
from .simulation_models import DecisionSimulationReport, DecisionTrialResult


class AuditedDecisionTrialResult(DecisionTrialResult):
    """Decision result plus the exact typed retrieval output used by the policy."""

    returned_lessons: list[RetrievedLessonSnapshot] = Field(default_factory=list)
    filter_counts: RecallFilterCounts | None = None

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
        if self.strategy is StrategyName.OSOZNANIE_RECALL:
            if self.filter_counts is None:
                raise ValueError("Osoznanie trial requires filter diagnostics")
        elif self.filter_counts is not None:
            raise ValueError(
                "strategies without a structured filter pipeline require null diagnostics"
            )
        return self


class StructuredDecisionSimulationReport(DecisionSimulationReport):
    claim: SyntheticClaim
    trial_results: list[AuditedDecisionTrialResult]
