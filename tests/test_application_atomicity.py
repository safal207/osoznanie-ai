from datetime import UTC, datetime, timedelta

import pytest

from osoznanie.application import (
    CriterionOperator,
    LessonApplication,
    SuccessCriterion,
)
from osoznanie.models import Event, Hypothesis, Lesson, Reflection
from osoznanie.storage import IdempotencyConflictError, SQLiteExperienceStore

NOW = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)


def _seed(store: SQLiteExperienceStore) -> None:
    store.save(
        SuccessCriterion(
            id="crt_checkout",
            name="Checkout completes",
            definition="The checkout completion signal is true.",
            definition_version="checkout-completes-v1",
            evaluator_type="deterministic-comparison",
            evaluator_version="comparison-v1",
            observation_window_seconds=300,
            metric_key="checkout_completed",
            operator=CriterionOperator.EQ,
            expected_value=True,
            fixed_at=NOW,
            created_at=NOW,
        )
    )
    source = store.save(
        Event(
            id="evt_source",
            actor_ids=["agent_qa"],
            summary="A previous checkout release failed.",
            timestamp=NOW,
            created_at=NOW,
        )
    )
    reflection = store.save(
        Reflection(
            id="ref_checkout",
            source_ids=[source.id],
            hypotheses=[
                Hypothesis(
                    statement="The mobile browser matrix was omitted.",
                    confidence=0.9,
                )
            ],
            created_at=NOW,
        )
    )
    store.save(
        Lesson(
            id="les_checkout",
            statement="Test mobile browsers before release approval.",
            source_reflection_ids=[reflection.id],
            confidence=0.9,
            effective_from=NOW,
            created_at=NOW,
        )
    )
    for record_id, minute in (
        ("qry_checkout", 1),
        ("ret_checkout", 2),
        ("env_checkout", 2),
        ("act_checkout", 3),
    ):
        moment = NOW + timedelta(minutes=minute)
        store.save(
            Event(
                id=record_id,
                actor_ids=["agent_qa"],
                summary=f"Synthetic protocol record {record_id}.",
                timestamp=moment,
                created_at=moment,
            )
        )


def _application(record_id: str, *, actor_id: str) -> LessonApplication:
    return LessonApplication(
        id=record_id,
        lesson_id="les_checkout",
        recall_query_id="qry_checkout",
        retrieval_execution_id="ret_checkout",
        action_execution_id="act_checkout",
        success_criterion_id="crt_checkout",
        environment_snapshot_id="env_checkout",
        actor_id=actor_id,
        applied_at=NOW + timedelta(minutes=4),
        idempotency_key="checkout-run-001",
        created_at=NOW + timedelta(minutes=4),
    )


def test_idempotency_insert_failure_rolls_back_orphan_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SQLiteExperienceStore()
    _seed(store)
    original = store.save(_application("app_original", actor_id="agent_qa"))

    monkeypatch.setattr(
        store,
        "_existing_idempotent_application",
        lambda _record: None,
    )
    conflict = _application("app_conflict", actor_id="agent_other")

    with pytest.raises(IdempotencyConflictError):
        store.save(conflict)

    assert not store.exists(conflict.id)
    assert store.list("lesson_application") == [original]
