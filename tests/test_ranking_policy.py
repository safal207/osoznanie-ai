from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from osoznanie.models import (
    AccessPolicy,
    Evidence,
    Hypothesis,
    Lesson,
    Reflection,
    TrustLevel,
    ValidationStatus,
)
from osoznanie.recall import (
    ACTIVE_RANKING_POLICY_ID,
    SCORE_FORMULA_VERSION,
    RecallEngine,
    RecallQuery,
    RiskLevel,
    canonical_score_bucket,
    get_ranking_policy,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class StaticRecallStore:
    def __init__(self, records: list) -> None:
        self.records = list(records)
        self.by_id = {record.id: record for record in records}

    def get(self, record_id: str):
        return self.by_id[record_id]

    def list(self, record_type: str | None = None):
        if record_type is None:
            return list(self.records)
        return [record for record in self.records if record.type == record_type]


def query() -> RecallQuery:
    return RecallQuery(
        agent_id="agent_qa",
        requester_id="human_alexey",
        domain="quality-assurance",
        task_type="checkout-release-validation",
        tags=["checkout", "chrome"],
        risk_level=RiskLevel.HIGH,
    )


def shared_context() -> tuple[Evidence, Reflection]:
    evidence = Evidence(
        id="evd_shared",
        source_type="incident-report",
        uri="demo://evidence/shared",
        trust_level=TrustLevel.VERIFIED,
        access_policy=AccessPolicy.PUBLIC,
        agent_id="agent_qa",
    )
    reflection = Reflection(
        id="ref_shared",
        source_ids=[evidence.id],
        hypotheses=[
            Hypothesis(
                statement="The supported mobile matrix was omitted.",
                confidence=0.92,
                evidence_ids=[evidence.id],
            )
        ],
        validation_status=ValidationStatus.HUMAN_APPROVED,
    )
    return evidence, reflection


def lesson(
    lesson_id: str,
    *,
    confidence: float,
    effective_from: datetime,
) -> Lesson:
    return Lesson(
        id=lesson_id,
        statement=f"Lesson {lesson_id}",
        scope={
            "domain": "quality-assurance",
            "task_types": ["checkout-release-validation"],
            "tags": ["checkout", "chrome", "release"],
        },
        source_reflection_ids=["ref_shared"],
        confidence=confidence,
        validation_status=ValidationStatus.HUMAN_APPROVED,
        effective_from=effective_from,
    )


def ranked_ids(lessons: list[Lesson]) -> tuple[list[str], list[float]]:
    evidence, reflection = shared_context()
    store = StaticRecallStore([evidence, reflection, *lessons])
    results = RecallEngine(store).recall(query(), now=NOW)
    return [result.lesson_id for result in results], [result.score for result in results]


def test_active_ranking_policy_is_registered_and_bound_to_scoring() -> None:
    policy = get_ranking_policy()

    assert policy.id == ACTIVE_RANKING_POLICY_ID
    assert policy.score_formula_version == SCORE_FORMULA_VERSION
    assert policy.score_bucket_width == Decimal("0.000001")
    assert policy.score_order == "desc"
    assert policy.tie_break_fields == ("lesson_id",)
    assert canonical_score_bucket(0.876543) == 876543
    assert canonical_score_bucket(0.876543) == canonical_score_bucket(0.876543)

    with pytest.raises(ValueError, match="unknown ranking policy"):
        get_ranking_policy("recall-ranking-unknown-v99")


def test_equal_bucket_ignores_confidence_as_secondary_signal() -> None:
    lower_confidence = lesson(
        "les_a",
        confidence=0.9,
        effective_from=NOW - timedelta(days=30),
    )
    higher_confidence = lesson(
        "les_z",
        confidence=0.9000001,
        effective_from=NOW - timedelta(days=30),
    )

    ids, scores = ranked_ids([higher_confidence, lower_confidence])

    assert scores[0] == scores[1]
    assert ids == ["les_a", "les_z"]


def test_equal_bucket_ignores_effective_from_as_secondary_signal() -> None:
    older = lesson(
        "les_a",
        confidence=0.9,
        effective_from=NOW - timedelta(days=30, seconds=1),
    )
    newer = lesson(
        "les_z",
        confidence=0.9,
        effective_from=NOW - timedelta(days=30),
    )

    ids, scores = ranked_ids([newer, older])

    assert scores[0] == scores[1]
    assert ids == ["les_a", "les_z"]


def test_ranking_is_independent_of_storage_insertion_order() -> None:
    first = lesson(
        "les_a",
        confidence=0.9,
        effective_from=NOW - timedelta(days=30),
    )
    second = lesson(
        "les_z",
        confidence=0.9000001,
        effective_from=NOW - timedelta(days=30),
    )

    forward_ids, forward_scores = ranked_ids([first, second])
    reverse_ids, reverse_scores = ranked_ids([second, first])

    assert forward_ids == reverse_ids == ["les_a", "les_z"]
    assert forward_scores == reverse_scores
