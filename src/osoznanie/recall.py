"""Deterministic scoped experience retrieval for Osoznanie."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from enum import StrEnum
from statistics import fmean
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import (
    AccessPolicy,
    Evidence,
    Lesson,
    Reflection,
    TrustLevel,
    ValidationStatus,
)
from .storage import RecordNotFoundError, SQLiteExperienceStore

SCOPE_GATE = 0.30
RECENCY_HALF_LIFE_DAYS = 365.0


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
    final_score: float = Field(ge=0.0, le=1.0)


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


def _normalized(value: Any) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _normalized_list(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {_normalized(item) for item in value if _normalized(item)}


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _scope_score(query: RecallQuery, lesson: Lesson) -> _ScopeScore:
    domain = _normalized(lesson.scope.get("domain"))
    task_types = _normalized_list(lesson.scope.get("task_types"))
    tags = _normalized_list(lesson.scope.get("tags"))

    domain_match = 1.0 if domain == query.domain else 0.0
    task_match = 1.0 if query.task_type in task_types else 0.0
    tag_match = _jaccard(set(query.tags), tags)
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


def _access_allowed(query: RecallQuery, evidence: Evidence) -> bool:
    if evidence.tenant_id is not None and evidence.tenant_id.lower() != query.tenant_id:
        return False

    if evidence.access_policy is AccessPolicy.PUBLIC:
        return True
    if evidence.access_policy is AccessPolicy.PRIVATE:
        return evidence.owner_id is not None and evidence.owner_id.lower() == query.requester_id
    if evidence.access_policy is AccessPolicy.OWNER_AND_AGENT:
        owner_allowed = (
            evidence.owner_id is not None and evidence.owner_id.lower() == query.requester_id
        )
        agent_allowed = (
            evidence.agent_id is not None and evidence.agent_id.lower() == query.agent_id
        )
        return owner_allowed or agent_allowed

    return False


def _record_ref(store: SQLiteExperienceStore, record_id: str) -> ProvenanceRef | None:
    try:
        record = store.get(record_id)
        record_type = ProvenanceType(record.type)
    except (RecordNotFoundError, ValueError):
        return None
    return ProvenanceRef(id=record.id, type=record_type)


def _lesson_context(
    store: SQLiteExperienceStore,
    lesson: Lesson,
) -> tuple[list[Evidence], list[ProvenanceRef]]:
    evidence_by_id: dict[str, Evidence] = {}
    provenance: list[ProvenanceRef] = [
        ProvenanceRef(id=lesson.id, type=ProvenanceType.LESSON)
    ]
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
            reflection = store.get(reflection_id)
        except RecordNotFoundError:
            continue
        if not isinstance(reflection, Reflection):
            continue

        add_ref(ProvenanceRef(id=reflection.id, type=ProvenanceType.REFLECTION))
        for source_id in reflection.source_ids:
            add_ref(_record_ref(store, source_id))

        for hypothesis in reflection.hypotheses:
            for evidence_id in hypothesis.evidence_ids:
                try:
                    evidence = store.get(evidence_id)
                except RecordNotFoundError:
                    continue
                if isinstance(evidence, Evidence):
                    evidence_by_id[evidence.id] = evidence

    for evidence_id in sorted(evidence_by_id):
        add_ref(ProvenanceRef(id=evidence_id, type=ProvenanceType.EVIDENCE))

    return list(evidence_by_id.values()), provenance


def _reason_codes(
    query: RecallQuery,
    lesson: Lesson,
    scope: _ScopeScore,
    evidence: list[Evidence],
    recency: float,
) -> list[ReasonCode]:
    codes: list[ReasonCode] = []
    if scope.domain_match == 1.0:
        codes.append(ReasonCode.DOMAIN_MATCH)
    if scope.task_match == 1.0:
        codes.append(ReasonCode.EXACT_TASK_TYPE_MATCH)
    if scope.tag_match > 0.0:
        codes.append(ReasonCode.TAG_OVERLAP)
    if lesson.validation_status is ValidationStatus.HUMAN_APPROVED:
        codes.append(ReasonCode.HUMAN_APPROVED)
    if lesson.validation_status is ValidationStatus.ACTIVE:
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


def _explanation(query: RecallQuery, codes: list[ReasonCode]) -> str:
    reasons: list[str] = []
    if ReasonCode.EXACT_TASK_TYPE_MATCH in codes:
        reasons.append(f'exactly matches task type "{query.task_type}"')
    elif ReasonCode.DOMAIN_MATCH in codes:
        reasons.append(f'matches domain "{query.domain}"')
    if ReasonCode.TAG_OVERLAP in codes:
        reasons.append("shares task tags")
    if ReasonCode.HUMAN_APPROVED in codes:
        reasons.append("is human-approved")
    elif ReasonCode.ACTIVE_LESSON in codes:
        reasons.append("is active")
    if ReasonCode.VERIFIED_EVIDENCE in codes:
        reasons.append("is supported by verified evidence")
    elif ReasonCode.REPORTED_EVIDENCE in codes:
        reasons.append("is supported by reported evidence")
    if ReasonCode.RECENT_LESSON in codes:
        reasons.append("remains recent under the 365-day half-life policy")
    return "Selected because it " + ", ".join(reasons) + "."


def recall(
    store: SQLiteExperienceStore,
    query: RecallQuery,
    *,
    now: datetime | None = None,
) -> list[RecallResult]:
    """Return accessible, relevant lessons using deterministic ranking."""
    effective_now = now or datetime.now(UTC)
    if effective_now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    candidates: list[tuple[RecallResult, Lesson]] = []
    for record in store.list("lesson"):
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
        if scope.final < SCOPE_GATE:
            continue

        evidence, provenance = _lesson_context(store, lesson)
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

        codes = _reason_codes(query, lesson, scope, evidence, recency)
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
                final_score=rounded_score,
            ),
            reason_codes=codes,
            provenance=provenance,
            explanation=_explanation(query, codes),
        )
        candidates.append((result, lesson))

    candidates.sort(
        key=lambda item: (
            -item[0].score,
            -item[1].confidence,
            -item[1].effective_from.timestamp(),
            item[1].id,
        )
    )
    return [result for result, _ in candidates[: query.max_items]]
