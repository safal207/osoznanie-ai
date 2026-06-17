"""Deterministic scoped experience retrieval for Osoznanie."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from statistics import fmean
from types import MappingProxyType
from typing import Mapping, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import (
    AccessPolicy,
    Evidence,
    Lesson,
    Record,
    Reflection,
    TrustLevel,
    ValidationStatus,
)
from .storage import RecordNotFoundError

SCOPE_GATE = 0.30
RECENCY_HALF_LIFE_DAYS = 365.0
SCORE_FORMULA_VERSION = "scoring-v0.1"
ACTIVE_RANKING_POLICY_ID = "recall-ranking-v0.2"


@dataclass(frozen=True)
class RankingPolicySpec:
    """Versioned deterministic ordering contract for recall results."""

    id: str
    score_formula_version: str
    score_bucket_width: Decimal
    score_order: str
    tie_break_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("ranking policy id must not be blank")
        if not self.score_formula_version.strip():
            raise ValueError("score formula version must not be blank")
        if self.score_bucket_width <= 0:
            raise ValueError("score bucket width must be positive")
        if self.score_order not in {"asc", "desc"}:
            raise ValueError("score order must be 'asc' or 'desc'")
        if not self.tie_break_fields:
            raise ValueError("at least one tie-break field is required")


RANKING_POLICIES: Mapping[str, RankingPolicySpec] = MappingProxyType(
    {
        ACTIVE_RANKING_POLICY_ID: RankingPolicySpec(
            id=ACTIVE_RANKING_POLICY_ID,
            score_formula_version=SCORE_FORMULA_VERSION,
            score_bucket_width=Decimal("0.000001"),
            score_order="desc",
            tie_break_fields=("lesson_id",),
        )
    }
)


def get_ranking_policy(
    policy_id: str = ACTIVE_RANKING_POLICY_ID,
) -> RankingPolicySpec:
    """Return a registered ranking policy or reject an unknown policy ID."""

    try:
        policy = RANKING_POLICIES[policy_id]
    except KeyError as error:
        raise ValueError(f"unknown ranking policy: {policy_id}") from error
    if policy.score_formula_version != SCORE_FORMULA_VERSION:
        raise ValueError(
            "ranking policy score formula does not match the active score formula"
        )
    return policy


def canonical_score_bucket(
    public_score: float,
    policy_id: str = ACTIVE_RANKING_POLICY_ID,
) -> int:
    """Map the public six-decimal score to a deterministic Decimal bucket."""

    policy = get_ranking_policy(policy_id)
    score = Decimal(str(public_score))
    bucket = (score / policy.score_bucket_width).to_integral_value(
        rounding=ROUND_HALF_UP
    )
    return int(bucket)


class RecallStore(Protocol):
    """Storage contract required by RecallEngine."""

    def get(self, record_id: str) -> Record: ...

    def list(self, record_type: str | None = None) -> list[Record]: ...


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ReasonCode(StrEnum):
    DOMAIN_MATCH = "domain_match"
    EXACT_TASK_TYPE_MATCH = "exact_task_type_match"
    TAG_OVERLAP = "tag_overlap"
    HUMAN_APPROVED = "human_approved"
    ACTIVE_LESSON = "active_lesson"
    REPORTED_EVIDENCE = "reported_evidence"
    VERIFIED_EVIDENCE = "verified_evidence"
    RECENT_LESSON = "recent_lesson"
    HIGH_RISK_THRESHOLD_PASSED = "high_risk_threshold_passed"


class ProvenanceType(StrEnum):
    EVIDENCE = "evidence"
    EVENT = "event"
    DECISION = "decision"
    OUTCOME = "outcome"
    REFLECTION = "reflection"
    LESSON = "lesson"


class RecallQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    requester_id: str = Field(min_length=1)
    tenant_id: str | None = None
    domain: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.MEDIUM
    max_items: int = Field(default=10, ge=1, le=50)

    @field_validator("agent_id", "requester_id", "tenant_id", "domain", "task_type")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        return sorted({value.strip().lower() for value in values if value.strip()})


class ScoreBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_match: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_trust: float = Field(ge=0.0, le=1.0)
    recency: float = Field(ge=0.0, le=1.0)


class ProvenanceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: ProvenanceType


class RecallResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lesson_id: str
    statement: str
    score: float = Field(ge=0.0, le=1.0)
    score_breakdown: ScoreBreakdown
    reason_codes: list[ReasonCode]
    provenance: list[ProvenanceRef]
    explanation: str


class _ScopeScore(BaseModel):
    domain_match: float
    task_match: float
    tag_match: float
    final: float


TRUST_SCORES = {
    TrustLevel.UNTRUSTED: 0.0,
    TrustLevel.REPORTED: 0.5,
    TrustLevel.VERIFIED: 1.0,
}

MIN_SCORE = {
    RiskLevel.LOW: 0.35,
    RiskLevel.MEDIUM: 0.45,
    RiskLevel.HIGH: 0.55,
}


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _scope_score(query: RecallQuery, lesson: Lesson) -> _ScopeScore:
    domain_match = 1.0 if lesson.scope.domain == query.domain else 0.0
    task_match = 1.0 if query.task_type in lesson.scope.task_types else 0.0
    tag_match = _jaccard(set(query.tags), set(lesson.scope.tags))
    final = 0.30 * domain_match + 0.50 * task_match + 0.20 * tag_match
    return _ScopeScore(
        domain_match=domain_match,
        task_match=task_match,
        tag_match=tag_match,
        final=final,
    )


def _recency_score(lesson: Lesson, now: datetime) -> float:
    age_seconds = max(0.0, (now - lesson.effective_from).total_seconds())
    age_days = age_seconds / 86_400.0
    return math.exp(-math.log(2.0) * age_days / RECENCY_HALF_LIFE_DAYS)


def _normalized(value: str | None) -> str | None:
    return value.strip().lower() if value is not None else None


def _access_allowed(query: RecallQuery, evidence: Evidence) -> bool:
    evidence_tenant = _normalized(evidence.tenant_id)
    if evidence_tenant is not None and evidence_tenant != query.tenant_id:
        return False

    if evidence.access_policy is AccessPolicy.PUBLIC:
        return True
    if evidence.access_policy is AccessPolicy.PRIVATE:
        return _normalized(evidence.owner_id) == query.requester_id
    if evidence.access_policy is AccessPolicy.OWNER_AND_AGENT:
        return (
            _normalized(evidence.owner_id) == query.requester_id
            or _normalized(evidence.agent_id) == query.agent_id
        )

    return False


class RecallEngine:
    """Filter and rank lessons using an injected storage implementation."""

    def __init__(self, store: RecallStore) -> None:
        self.store = store

    def _record_ref(self, record_id: str) -> ProvenanceRef | None:
        try:
            record = self.store.get(record_id)
            record_type = ProvenanceType(record.type)
        except (RecordNotFoundError, ValueError):
            return None
        return ProvenanceRef(id=record.id, type=record_type)

    def _lesson_context(
        self,
        lesson: Lesson,
    ) -> tuple[list[Evidence], list[ProvenanceRef]]:
        evidence_by_id: dict[str, Evidence] = {}
        provenance = [ProvenanceRef(id=lesson.id, type=ProvenanceType.LESSON)]
        seen_refs = {(lesson.id, ProvenanceType.LESSON)}

        def add_ref(reference: ProvenanceRef | None) -> None:
            if reference is None:
                return
            key = (reference.id, reference.type)
            if key not in seen_refs:
                provenance.append(reference)
                seen_refs.add(key)

        for reflection_id in lesson.source_reflection_ids:
            try:
                reflection = self.store.get(reflection_id)
            except RecordNotFoundError:
                continue
            if not isinstance(reflection, Reflection):
                continue

            add_ref(ProvenanceRef(id=reflection.id, type=ProvenanceType.REFLECTION))
            for source_id in reflection.source_ids:
                add_ref(self._record_ref(source_id))

            for hypothesis in reflection.hypotheses:
                for evidence_id in hypothesis.evidence_ids:
                    try:
                        evidence = self.store.get(evidence_id)
                    except RecordNotFoundError:
                        continue
                    if isinstance(evidence, Evidence):
                        evidence_by_id[evidence.id] = evidence

        for evidence_id in sorted(evidence_by_id):
            add_ref(ProvenanceRef(id=evidence_id, type=ProvenanceType.EVIDENCE))

        evidence = [evidence_by_id[key] for key in sorted(evidence_by_id)]
        return evidence, provenance

    @staticmethod
    def _reason_codes(
        query: RecallQuery,
        lesson: Lesson,
        scope: _ScopeScore,
        evidence: list[Evidence],
        recency: float,
    ) -> list[ReasonCode]:
        codes = [ReasonCode.DOMAIN_MATCH]
        if scope.task_match == 1.0:
            codes.append(ReasonCode.EXACT_TASK_TYPE_MATCH)
        if scope.tag_match > 0.0:
            codes.append(ReasonCode.TAG_OVERLAP)
        if lesson.validation_status is ValidationStatus.HUMAN_APPROVED:
            codes.append(ReasonCode.HUMAN_APPROVED)
        else:
            codes.append(ReasonCode.ACTIVE_LESSON)
        if any(item.trust_level is TrustLevel.VERIFIED for item in evidence):
            codes.append(ReasonCode.VERIFIED_EVIDENCE)
        if any(item.trust_level is TrustLevel.REPORTED for item in evidence):
            codes.append(ReasonCode.REPORTED_EVIDENCE)
        if recency >= 0.5:
            codes.append(ReasonCode.RECENT_LESSON)
        if query.risk_level is RiskLevel.HIGH:
            codes.append(ReasonCode.HIGH_RISK_THRESHOLD_PASSED)
        return codes

    @staticmethod
    def _explanation(query: RecallQuery, codes: list[ReasonCode]) -> str:
        reasons: list[str] = []
        if ReasonCode.EXACT_TASK_TYPE_MATCH in codes:
            reasons.append(f'exactly matches task type "{query.task_type}"')
        else:
            reasons.append(f'matches domain "{query.domain}" and shares task tags')
        if ReasonCode.HUMAN_APPROVED in codes:
            reasons.append("is human-approved")
        else:
            reasons.append("is active")
        if ReasonCode.VERIFIED_EVIDENCE in codes:
            reasons.append("is supported by verified evidence")
        elif ReasonCode.REPORTED_EVIDENCE in codes:
            reasons.append("is supported by reported evidence")
        if ReasonCode.RECENT_LESSON in codes:
            reasons.append("remains recent under the 365-day half-life policy")
        return "Selected because it " + ", ".join(reasons) + "."

    def recall(
        self,
        query: RecallQuery,
        *,
        now: datetime | None = None,
    ) -> list[RecallResult]:
        """Return accessible, relevant lessons using deterministic ranking."""

        effective_now = now or datetime.now(UTC)
        if effective_now.tzinfo is None:
            raise ValueError("now must be timezone-aware")

        policy = get_ranking_policy()
        candidates: list[tuple[RecallResult, int]] = []
        for record in self.store.list("lesson"):
            if not isinstance(record, Lesson):
                continue
            lesson = record

            if lesson.validation_status not in {
                ValidationStatus.HUMAN_APPROVED,
                ValidationStatus.ACTIVE,
            }:
                continue
            if lesson.effective_from > effective_now:
                continue
            if lesson.expires_at is not None and lesson.expires_at <= effective_now:
                continue

            scope = _scope_score(query, lesson)
            if scope.domain_match != 1.0 or scope.final <= SCOPE_GATE:
                continue

            evidence, provenance = self._lesson_context(lesson)
            if any(not _access_allowed(query, item) for item in evidence):
                continue

            evidence_trust = (
                fmean(TRUST_SCORES[item.trust_level] for item in evidence)
                if evidence
                else 0.0
            )
            recency = _recency_score(lesson, effective_now)
            final_score = (
                0.55 * scope.final
                + 0.20 * lesson.confidence
                + 0.15 * evidence_trust
                + 0.10 * recency
            )
            if final_score < MIN_SCORE[query.risk_level]:
                continue

            codes = self._reason_codes(query, lesson, scope, evidence, recency)
            rounded_score = round(final_score, 6)
            result = RecallResult(
                lesson_id=lesson.id,
                statement=lesson.statement,
                score=rounded_score,
                score_breakdown=ScoreBreakdown(
                    scope_match=round(scope.final, 6),
                    confidence=round(lesson.confidence, 6),
                    evidence_trust=round(evidence_trust, 6),
                    recency=round(recency, 6),
                ),
                reason_codes=codes,
                provenance=provenance,
                explanation=self._explanation(query, codes),
            )
            candidates.append(
                (result, canonical_score_bucket(result.score, policy.id))
            )

        candidates.sort(key=lambda item: (-item[1], item[0].lesson_id))
        return [result for result, _ in candidates[: query.max_items]]
