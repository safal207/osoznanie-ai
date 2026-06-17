from datetime import UTC, datetime, timedelta

import pytest

from osoznanie.models import (
    Commitment,
    Decision,
    Evidence,
    Event,
    Hypothesis,
    IdentitySnapshot,
    Lesson,
    Outcome,
    OutcomeStatus,
    Reflection,
    Trait,
    TrustLevel,
    ValidationStatus,
)
from osoznanie.storage import (
    MissingReferenceError,
    ReferencedRecordError,
    SQLiteExperienceStore,
)


def test_complete_qa_experience_cycle(tmp_path) -> None:
    store = SQLiteExperienceStore(tmp_path / "experience.db")

    evidence = store.save(
        Evidence(
            source_type="test-report",
            uri="internal://reports/checkout-incident",
            content_hash="sha256:checkout-incident",
            trust_level=TrustLevel.VERIFIED,
        )
    )
    event = store.save(
        Event(
            actor_ids=["agent_qa", "human_alexey"],
            summary="Checkout failed on a supported Android Chrome configuration.",
            context={"feature": "checkout", "browser": "Chrome", "platform": "Android"},
            evidence_ids=[evidence.id],
        )
    )
    decision = store.save(
        Decision(
            event_id=event.id,
            agent_id="agent_qa",
            chosen_action="Approve the release after desktop Chrome smoke testing.",
            alternatives_considered=["Run the full browser-device matrix"],
            reasoning_summary="Desktop smoke tests passed and mobile risk was underestimated.",
            evidence_ids=[evidence.id],
            confidence=0.63,
        )
    )
    outcome = store.save(
        Outcome(
            decision_id=decision.id,
            status=OutcomeStatus.FAILURE,
            summary="Customers on Android Chrome could not complete checkout.",
            impact={"severity": "high", "affected_users": "subset"},
            evidence_ids=[evidence.id],
        )
    )
    reflection = store.save(
        Reflection(
            source_ids=[event.id, decision.id, outcome.id],
            hypotheses=[
                Hypothesis(
                    statement="The release decision omitted a supported mobile configuration.",
                    confidence=0.92,
                    evidence_ids=[evidence.id],
                )
            ],
            limitations=["Engineering logs are still required to confirm the code-level cause."],
            validation_status=ValidationStatus.HUMAN_APPROVED,
        )
    )
    lesson = store.save(
        Lesson(
            statement=(
                "Before approving customer-critical checkout changes, test the supported "
                "browser-device matrix rather than desktop Chrome alone."
            ),
            scope={"domain": "quality-assurance", "task": "checkout-release-validation"},
            source_reflection_ids=[reflection.id],
            confidence=0.88,
            validation_status=ValidationStatus.HUMAN_APPROVED,
        )
    )
    commitment = store.save(
        Commitment(
            agent_id="agent_qa",
            counterparty_ids=["human_alexey"],
            statement="Include mobile Chrome in the next checkout regression plan.",
            created_from_ids=[lesson.id],
            due_at=datetime.now(UTC) + timedelta(days=7),
        )
    )
    trait = store.save(
        Trait(
            name="cross-platform caution",
            description=(
                "Prefers representative browser-device coverage for customer-critical flows."
            ),
            value=0.72,
            source_lesson_ids=[lesson.id],
            confidence=0.81,
            validation_status=ValidationStatus.HUMAN_APPROVED,
        )
    )
    snapshot = store.save(
        IdentitySnapshot(
            agent_id="agent_qa",
            version=1,
            core_constraints=["Do not fabricate test evidence."],
            active_trait_ids=[trait.id],
            active_lesson_ids=[lesson.id],
            open_commitment_ids=[commitment.id],
            change_summary="Cross-platform caution increased after the checkout incident.",
            approved_by=["human_alexey"],
        )
    )

    loaded = store.get(snapshot.id)
    assert loaded == snapshot
    assert len(store.list()) == 9

    explanation = store.explain(snapshot.id)
    reference_ids = {item["id"] for item in explanation["references"]}
    assert reference_ids == {trait.id, lesson.id, commitment.id}


def test_missing_provenance_is_rejected() -> None:
    store = SQLiteExperienceStore()
    orphan = Decision(
        event_id="evt_missing",
        agent_id="agent_qa",
        chosen_action="Approve release.",
        reasoning_summary="Smoke tests passed.",
        confidence=0.5,
    )

    with pytest.raises(MissingReferenceError, match="evt_missing"):
        store.save(orphan)


def test_referenced_records_cannot_be_deleted_without_force() -> None:
    store = SQLiteExperienceStore()
    evidence = store.save(Evidence(source_type="log", uri="internal://log/1"))
    event = store.save(
        Event(
            actor_ids=["agent_qa"],
            summary="A test event.",
            evidence_ids=[evidence.id],
        )
    )

    with pytest.raises(ReferencedRecordError, match=event.id):
        store.delete(evidence.id)
