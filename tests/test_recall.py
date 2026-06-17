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
from osoznanie.recall import (
    ProvenanceType,
    ReasonCode,
    RecallEngine,
    RecallQuery,
    RiskLevel,
)
from osoznanie.storage import SQLiteExperienceStore

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def seed_lesson(
    store: SQLiteExperienceStore,
    *,
    statement: str = "Test supported mobile browsers before approving checkout releases.",
    scope: dict | None = None,
    confidence: float = 0.9,
    status: ValidationStatus = ValidationStatus.HUMAN_APPROVED,
    effective_from: datetime | None = None,
    expires_at: datetime | None = None,
    access_policy: AccessPolicy = AccessPolicy.PUBLIC,
    owner_id: str | None = None,
    agent_id: str | None = "agent_qa",
    tenant_id: str | None = None,
    trust_level: TrustLevel = TrustLevel.VERIFIED,
) -> Lesson:
    suffix = len(store.list())
    evidence = store.save(
        Evidence(
            source_type="incident-report",
            uri=f"demo://evidence/{suffix}",
            trust_level=trust_level,
            access_policy=access_policy,
            owner_id=owner_id,
            agent_id=agent_id,
            tenant_id=tenant_id,
        )
    )
    event = store.save(
        Event(
            actor_ids=["agent_qa", "human_alexey"],
            summary="Checkout failed on Android Chrome.",
            evidence_ids=[evidence.id],
        )
    )
    decision = store.save(
        Decision(
            event_id=event.id,
            agent_id="agent_qa",
            chosen_action="Approve after desktop-only validation.",
            reasoning_summary="Desktop smoke tests passed.",
            evidence_ids=[evidence.id],
            confidence=0.6,
        )
    )
    outcome = store.save(
        Outcome(
            decision_id=decision.id,
            status=OutcomeStatus.FAILURE,
            summary="Mobile customers could not complete checkout.",
            evidence_ids=[evidence.id],
        )
    )
    reflection = store.save(
        Reflection(
            source_ids=[event.id, decision.id, outcome.id],
            hypotheses=[
                Hypothesis(
                    statement="The supported mobile matrix was omitted.",
                    confidence=0.92,
                    evidence_ids=[evidence.id],
                )
            ],
            validation_status=ValidationStatus.HUMAN_APPROVED,
        )
    )
    return store.save(
        Lesson(
            statement=statement,
            scope=scope
            or {
                "domain": "quality-assurance",
                "task_types": ["checkout-release-validation"],
                "tags": ["checkout", "chrome", "release"],
            },
            source_reflection_ids=[reflection.id],
            confidence=confidence,
            validation_status=status,
            effective_from=effective_from or NOW - timedelta(days=30),
            expires_at=expires_at,
        )
    )


def query(**overrides) -> RecallQuery:
    values = {
        "agent_id": "agent_qa",
        "requester_id": "human_alexey",
        "domain": "quality-assurance",
        "task_type": "checkout-release-validation",
        "tags": ["checkout", "chrome"],
        "risk_level": RiskLevel.HIGH,
    }
    values.update(overrides)
    return RecallQuery(**values)


def test_exact_task_recall_is_explainable_and_deterministic() -> None:
    store = SQLiteExperienceStore()
    lesson = seed_lesson(store)
    engine = RecallEngine(store)

    first = engine.recall(query(), now=NOW)
    second = engine.recall(query(), now=NOW)

    assert first == second
    assert len(first) == 1
    result = first[0]
    assert result.lesson_id == lesson.id
    assert ReasonCode.EXACT_TASK_TYPE_MATCH in result.reason_codes
    assert ReasonCode.HUMAN_APPROVED in result.reason_codes
    assert ReasonCode.VERIFIED_EVIDENCE in result.reason_codes
    assert "final_score" not in result.score_breakdown.model_dump()
    assert {item.type for item in result.provenance} == {
        ProvenanceType.LESSON,
        ProvenanceType.REFLECTION,
        ProvenanceType.EVENT,
        ProvenanceType.DECISION,
        ProvenanceType.OUTCOME,
        ProvenanceType.EVIDENCE,
    }
    assert result.explanation.startswith("Selected because it exactly matches task type")


def test_domain_only_match_is_rejected() -> None:
    store = SQLiteExperienceStore()
    seed_lesson(
        store,
        scope={
            "domain": "quality-assurance",
            "task_types": ["selenium-maintenance"],
            "tags": [],
        },
    )

    assert RecallEngine(store).recall(query(tags=[]), now=NOW) == []


def test_private_evidence_requires_owner() -> None:
    store = SQLiteExperienceStore()
    seed_lesson(
        store,
        access_policy=AccessPolicy.PRIVATE,
        owner_id="human_alexey",
        agent_id=None,
    )
    engine = RecallEngine(store)

    assert engine.recall(query(requester_id="other_user"), now=NOW) == []
    assert len(engine.recall(query(requester_id="human_alexey"), now=NOW)) == 1


def test_owner_and_agent_policy_allows_matching_agent() -> None:
    store = SQLiteExperienceStore()
    seed_lesson(
        store,
        access_policy=AccessPolicy.OWNER_AND_AGENT,
        owner_id="another_owner",
        agent_id="agent_qa",
    )

    results = RecallEngine(store).recall(query(requester_id="other_user"), now=NOW)
    assert len(results) == 1


def test_tenant_mismatch_denies_before_policy() -> None:
    store = SQLiteExperienceStore()
    seed_lesson(
        store,
        access_policy=AccessPolicy.PUBLIC,
        tenant_id="tenant_a",
    )
    engine = RecallEngine(store)

    assert engine.recall(query(tenant_id="tenant_b"), now=NOW) == []
    assert len(engine.recall(query(tenant_id="tenant_a"), now=NOW)) == 1


def test_expired_future_and_rejected_lessons_are_filtered() -> None:
    store = SQLiteExperienceStore()
    seed_lesson(
        store,
        statement="Expired",
        effective_from=NOW - timedelta(days=60),
        expires_at=NOW - timedelta(days=1),
    )
    seed_lesson(store, statement="Future", effective_from=NOW + timedelta(days=1))
    seed_lesson(store, statement="Rejected", status=ValidationStatus.REJECTED)

    assert RecallEngine(store).recall(query(), now=NOW) == []


def test_risk_threshold_changes_eligibility() -> None:
    store = SQLiteExperienceStore()
    seed_lesson(
        store,
        confidence=0.5,
        trust_level=TrustLevel.UNTRUSTED,
        scope={
            "domain": "quality-assurance",
            "task_types": ["other-task"],
            "tags": ["checkout", "chrome"],
        },
        effective_from=NOW,
    )
    engine = RecallEngine(store)

    assert len(engine.recall(query(risk_level=RiskLevel.MEDIUM), now=NOW)) == 1
    assert engine.recall(query(risk_level=RiskLevel.HIGH), now=NOW) == []


def test_max_items_applies_after_stable_sorting() -> None:
    store = SQLiteExperienceStore()
    lower = seed_lesson(store, statement="Lower confidence", confidence=0.7)
    higher = seed_lesson(store, statement="Higher confidence", confidence=0.95)

    results = RecallEngine(store).recall(query(max_items=1), now=NOW)
    assert [item.lesson_id for item in results] == [higher.id]
    assert results[0].lesson_id != lower.id


def test_query_normalizes_and_deduplicates_tags() -> None:
    item = query(tags=[" Chrome ", "checkout", "chrome", ""])
    assert item.tags == ["checkout", "chrome"]
