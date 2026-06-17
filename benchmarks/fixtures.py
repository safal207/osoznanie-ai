"""Deterministic repeated-error benchmark fixtures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from osoznanie.models import (
    AccessPolicy,
    Decision,
    Event,
    Evidence,
    Hypothesis,
    Lesson,
    Outcome,
    OutcomeStatus,
    Reflection,
    TrustLevel,
    ValidationStatus,
)
from osoznanie.recall import RecallQuery, RiskLevel
from osoznanie.storage import SQLiteExperienceStore

from .models import ErrorSignature, RetrievalBenchmarkScenario

BENCHMARK_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class BenchmarkCase:
    scenario: RetrievalBenchmarkScenario
    store: SQLiteExperienceStore


@dataclass(frozen=True)
class _LessonDefinition:
    lesson_id: str
    statement: str
    domain: str
    task_types: tuple[str, ...]
    tags: tuple[str, ...]
    status: ValidationStatus = ValidationStatus.HUMAN_APPROVED
    effective_from: datetime = BENCHMARK_NOW - timedelta(days=30)
    expires_at: datetime | None = None
    access_policy: AccessPolicy = AccessPolicy.PUBLIC


def _seed_lesson(store: SQLiteExperienceStore, definition: _LessonDefinition) -> Lesson:
    suffix = definition.lesson_id.removeprefix("les_")
    created_at = definition.effective_from

    evidence = store.save(
        Evidence(
            id=f"evd_{suffix}",
            created_at=created_at,
            source_type="benchmark-fixture",
            uri=f"benchmark://{suffix}",
            captured_at=created_at,
            trust_level=TrustLevel.VERIFIED,
            access_policy=definition.access_policy,
            owner_id="human_alexey",
            agent_id="agent_qa",
        )
    )
    event = store.save(
        Event(
            id=f"evt_{suffix}",
            created_at=created_at,
            timestamp=created_at,
            actor_ids=["agent_qa", "human_alexey"],
            summary=f"Benchmark event for {suffix}.",
            evidence_ids=[evidence.id],
        )
    )
    decision = store.save(
        Decision(
            id=f"dec_{suffix}",
            created_at=created_at,
            event_id=event.id,
            agent_id="agent_qa",
            chosen_action="Use the initial validation plan.",
            reasoning_summary="The benchmark reproduces a prior decision pattern.",
            evidence_ids=[evidence.id],
            confidence=0.6,
        )
    )
    outcome = store.save(
        Outcome(
            id=f"out_{suffix}",
            created_at=created_at,
            decision_id=decision.id,
            status=OutcomeStatus.FAILURE,
            summary="The benchmark outcome exposes a repeated-error pattern.",
            evidence_ids=[evidence.id],
            observed_at=created_at,
        )
    )
    reflection = store.save(
        Reflection(
            id=f"ref_{suffix}",
            created_at=created_at,
            source_ids=[event.id, decision.id, outcome.id],
            hypotheses=[
                Hypothesis(
                    statement="The selected lesson may prevent this error pattern.",
                    confidence=0.9,
                    evidence_ids=[evidence.id],
                )
            ],
            validation_status=ValidationStatus.HUMAN_APPROVED,
        )
    )
    return store.save(
        Lesson(
            id=definition.lesson_id,
            created_at=created_at,
            statement=definition.statement,
            scope={
                "domain": definition.domain,
                "task_types": list(definition.task_types),
                "tags": list(definition.tags),
            },
            source_reflection_ids=[reflection.id],
            confidence=0.9,
            validation_status=definition.status,
            effective_from=definition.effective_from,
            expires_at=definition.expires_at,
        )
    )


def _build_case(
    *,
    scenario_id: str,
    description: str,
    task_type: str,
    pattern_id: str,
    tags: list[str],
    relevant_statement: str,
    decoy_statement: str,
) -> BenchmarkCase:
    store = SQLiteExperienceStore()
    domain = "quality-assurance"
    relevant_id = f"les_{scenario_id}_relevant"
    decoy_ids = [
        f"les_{scenario_id}_access_denied",
        f"les_{scenario_id}_expired",
        f"les_{scenario_id}_proposed",
        f"les_{scenario_id}_wrong_domain",
    ]

    definitions = [
        _LessonDefinition(
            lesson_id=relevant_id,
            statement=relevant_statement,
            domain=domain,
            task_types=(task_type,),
            tags=tuple(tags),
        ),
        _LessonDefinition(
            lesson_id=decoy_ids[0],
            statement=decoy_statement,
            domain=domain,
            task_types=(task_type,),
            tags=tuple(tags),
            access_policy=AccessPolicy.TEAM,
        ),
        _LessonDefinition(
            lesson_id=decoy_ids[1],
            statement=decoy_statement,
            domain=domain,
            task_types=(task_type,),
            tags=tuple(tags),
            effective_from=BENCHMARK_NOW - timedelta(days=400),
            expires_at=BENCHMARK_NOW - timedelta(days=1),
        ),
        _LessonDefinition(
            lesson_id=decoy_ids[2],
            statement=decoy_statement,
            domain=domain,
            task_types=(task_type,),
            tags=tuple(tags),
            status=ValidationStatus.PROPOSED,
        ),
        _LessonDefinition(
            lesson_id=decoy_ids[3],
            statement=decoy_statement,
            domain="software-security",
            task_types=(task_type,),
            tags=tuple(tags),
        ),
    ]
    for definition in definitions:
        _seed_lesson(store, definition)

    query = RecallQuery(
        agent_id="agent_qa",
        requester_id="human_alexey",
        domain=domain,
        task_type=task_type,
        tags=tags,
        risk_level=RiskLevel.HIGH,
        max_items=5,
    )
    scenario = RetrievalBenchmarkScenario(
        scenario_id=scenario_id,
        description=description,
        query=query,
        error_signature=ErrorSignature(
            domain=domain,
            task_type=task_type,
            pattern_id=pattern_id,
        ),
        relevant_lesson_ids=[relevant_id],
        decoy_lesson_ids=decoy_ids,
    )
    return BenchmarkCase(scenario=scenario, store=store)


def build_benchmark_cases() -> list[BenchmarkCase]:
    """Create isolated stores so scenarios cannot leak lessons into each other."""
    return [
        _build_case(
            scenario_id="checkout_desktop_only",
            description="Desktop-only checkout validation misses Android Chrome.",
            task_type="checkout-release-validation",
            pattern_id="desktop-only-validation",
            tags=["checkout", "android", "chrome", "release"],
            relevant_statement=(
                "Use the supported browser-device matrix before release approval."
            ),
            decoy_statement=(
                "Quality assurance checkout release validation for Android Chrome."
            ),
        ),
        _build_case(
            scenario_id="transfer_timeout_status_split",
            description="A securities transfer timeout has inconsistent system statuses.",
            task_type="cross-system-transfer-timeout-analysis",
            pattern_id="inconsistent-status-timeout",
            tags=["integration", "timeout", "inconsistent", "status", "securities", "transfer"],
            relevant_statement="Compare every system checkpoint before escalation.",
            decoy_statement=(
                "Quality assurance cross system transfer timeout analysis for integration "
                "with inconsistent securities status."
            ),
        ),
        _build_case(
            scenario_id="incident_regression_omission",
            description="A release omits regression checks from a prior production incident.",
            task_type="production-regression-planning",
            pattern_id="incident-check-omission",
            tags=["regression", "incident", "release", "rollback", "checklist"],
            relevant_statement="Re-run incident-derived checks before sign-off.",
            decoy_statement=(
                "Quality assurance production regression planning incident release "
                "rollback checklist."
            ),
        ),
    ]
