"""Restricted audit artifact models for completed decision-path trials."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from osoznanie.recall import ProvenanceRef, ReasonCode, ScoreBreakdown

from .audit_policy import RankingPolicyRef
from .claims import SyntheticClaim
from .models import RetrievedLessonSnapshot, StrategyName
from .path_contracts import (
    DecisionPathReasonCode,
    DecisionPathStatus,
    validate_status_reason,
)
from .simulation_models import SimulatedDecision

AUDIT_VERSION = "restricted-decision-path-audit-v0.1"


class AuditRetrievedLesson(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lesson_id: str = Field(min_length=1)
    rank: int = Field(ge=1)
    canonical_score: Decimal
    score_breakdown: ScoreBreakdown | None = None
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    provenance_refs: list[ProvenanceRef] = Field(default_factory=list)

    @field_serializer("canonical_score")
    def serialize_score(self, value: Decimal) -> str:
        return format(value, "f")

    @classmethod
    def from_snapshot(
        cls,
        snapshot: RetrievedLessonSnapshot,
    ) -> AuditRetrievedLesson:
        return cls(
            lesson_id=snapshot.lesson_id,
            rank=snapshot.rank,
            canonical_score=Decimal(str(snapshot.score)),
            score_breakdown=snapshot.score_breakdown,
            reason_codes=snapshot.reason_codes,
            provenance_refs=snapshot.provenance_refs,
        )


class RestrictedDecisionPathAudit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    audit_version: str = AUDIT_VERSION
    graph_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    strategy: StrategyName
    policy_name: str = Field(min_length=1)
    claim: SyntheticClaim
    ranking_policy: RankingPolicyRef | None
    returned_lessons: list[AuditRetrievedLesson] = Field(default_factory=list)
    decision: SimulatedDecision
    status: DecisionPathStatus
    reason_code: DecisionPathReasonCode

    @model_validator(mode="after")
    def validate_contract(self) -> RestrictedDecisionPathAudit:
        validate_status_reason(self.status, self.reason_code)
        if self.strategy is StrategyName.NO_MEMORY:
            if self.ranking_policy is not None or self.returned_lessons:
                raise ValueError("no-memory audit cannot contain ranked lessons")
            return self
        if self.ranking_policy is None:
            raise ValueError("retrieval audit requires a ranking policy")
        if self.strategy is StrategyName.OSOZNANIE_RECALL:
            for lesson in self.returned_lessons:
                if lesson.score_breakdown is None:
                    raise ValueError("Osoznanie lesson requires score breakdown")
                if not lesson.provenance_refs:
                    raise ValueError("Osoznanie lesson requires provenance")
        if self.strategy is StrategyName.NAIVE_KEYWORD:
            for lesson in self.returned_lessons:
                if lesson.score_breakdown is not None:
                    raise ValueError("naive keyword has no score breakdown")
                if lesson.reason_codes or lesson.provenance_refs:
                    raise ValueError("naive keyword has no Recall metadata")
        return self
