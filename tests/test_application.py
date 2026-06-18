from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from osoznanie.application import (
    CriterionEvaluation,
    CriterionOperator,
    CriterionResult,
    EvaluationReasonCode,
    LessonApplication,
    ObservationValue,
    OutcomeObservation,
    SuccessCriterion,
)
from osoznanie.models import Event, Hypothesis, Lesson, Reflection
from osoznanie.storage import (
    ContractReferenceError,
    IdempotencyConflictError,
    MissingReferenceError,
    SQLiteExperienceStore,
    TemporalContractError,
)

NOW = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)


def criterion(**overrides) -> SuccessCriterion:
    values = {
        "id": "crt_checkout",
        "name": "Checkout completes",
        "definition": "The checkout completion signal is true.",
        "definition_version": "checkout-completes-v1",
        "evaluator_type": "deterministic-comparison",
        "evaluator_version": "comparison-v1",
        "observation_window_seconds": 300,
        "metric_key": "checkout_completed",
        "operator": CriterionOperator.EQ,
        "expected_value": True,
        "fixed_at": NOW,
        "created_at": NOW,
    }
    values.update(overrides)
    return SuccessCriterion(**values)


def seed_context(
    store: SQLiteExperienceStore,
    *,
    success_criterion: SuccessCriterion | None = None,
) -> dict[str, object]:
    effective_criterion = success_criterion or criterion()
    store.save(effective_criterion)

    source = store.save(
        Event(
            id="evt_source",
            actor_ids=["agent_qa"],
            summary="A previous checkout release failed on mobile Chrome.",
            created_at=NOW,
            timestamp=NOW,
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
    lesson = store.save(
        Lesson(
            id="les_checkout",
            statement="Test supported mobile browsers before release approval.",
            scope={
                "domain": "quality-assurance",
                "task_types": ["checkout-release-validation"],
                "tags": ["checkout", "chrome"],
            },
            source_reflection_ids=[reflection.id],
            confidence=0.9,
            effective_from=NOW,
            created_at=NOW,
        )
    )
    query = store.save(
        Event(
            id="qry_checkout",
            actor_ids=["agent_qa"],
            summary="Recall lessons for checkout release validation.",
            created_at=NOW + timedelta(minutes=1),
            timestamp=NOW + timedelta(minutes=1),
        )
    )
    retrieval = store.save(
        Event(
            id="ret_checkout",
            actor_ids=["agent_qa"],
            summary="Deterministic recall execution completed.",
            created_at=NOW + timedelta(minutes=2),
            timestamp=NOW + timedelta(minutes=2),
        )
    )
    action = store.save(
        Event(
            id="act_checkout",
            actor_ids=["agent_qa"],
            summary="Android Chrome regression was executed.",
            created_at=NOW + timedelta(minutes=3),
            timestamp=NOW + timedelta(minutes=3),
        )
    )
    environment = store.save(
        Event(
            id="env_checkout",
            actor_ids=["agent_qa"],
            summary="Environment snapshot for the checkout action.",
            created_at=NOW + timedelta(minutes=2),
            timestamp=NOW + timedelta(minutes=2),
        )
    )
    return {
        "criterion": effective_criterion,
        "lesson": lesson,
        "query": query,
        "retrieval": retrieval,
        "action": action,
        "environment": environment,
    }


def application(**overrides) -> LessonApplication:
    values = {
        "lesson_id": "les_checkout",
        "recall_query_id": "qry_checkout",
        "retrieval_execution_id": "ret_checkout",
        "action_execution_id": "act_checkout",
        "success_criterion_id": "crt_checkout",
        "environment_snapshot_id": "env_checkout",
        "actor_id": "agent_qa",
        "applied_at": NOW + timedelta(minutes=4),
        "idempotency_key": "checkout-run-001",
        "created_at": NOW + timedelta(minutes=4),
    }
    values.update(overrides)
    return LessonApplication(**values)


def observation(**overrides) -> OutcomeObservation:
    values = {
        "lesson_application_ids": ["app_checkout"],
        "action_execution_id": "act_checkout",
        "observed_at": NOW + timedelta(minutes=5),
        "values": [ObservationValue(key="checkout_completed", value=True)],
        "collection_policy_version": "checkout-observation-v1",
        "created_at": NOW + timedelta(minutes=5),
    }
    values.update(overrides)
    return OutcomeObservation(**values)


def test_complete_application_lifecycle_is_persisted_and_explainable() -> None:
    store = SQLiteExperienceStore()
    seed_context(store)
    applied = store.save(application(id="app_checkout"))
    observed = store.save(observation(id="obs_checkout"))
    evaluated = store.save(
        CriterionEvaluation(
            id="cev_checkout",
            criterion_id="crt_checkout",
            lesson_application_ids=[applied.id],
            observation_ids=[observed.id],
            result=CriterionResult.MET,
            evaluator_version="comparison-v1",
            evaluated_at=NOW + timedelta(minutes=6),
            created_at=NOW + timedelta(minutes=6),
        )
    )

    assert store.get(applied.id) == applied
    assert store.get(observed.id) == observed
    assert store.get(evaluated.id) == evaluated
    explanation = store.explain(evaluated.id)
    reference_ids = {item["id"] for item in explanation["references"]}
    assert reference_ids == {"crt_checkout", "app_checkout", "obs_checkout"}


def test_application_requires_all_references() -> None:
    store = SQLiteExperienceStore()
    with pytest.raises(MissingReferenceError, match="les_checkout"):
        store.save(application())


def test_success_criterion_must_be_fixed_before_query() -> None:
    store = SQLiteExperienceStore()
    late = criterion(fixed_at=NOW + timedelta(minutes=2))
    seed_context(store, success_criterion=late)

    with pytest.raises(TemporalContractError, match="fixed before recall query"):
        store.save(application())


def test_observation_cannot_predate_application() -> None:
    store = SQLiteExperienceStore()
    seed_context(store)
    applied = store.save(application(id="app_checkout"))

    with pytest.raises(TemporalContractError, match="must not predate"):
        store.save(
            observation(
                lesson_application_ids=[applied.id],
                observed_at=NOW + timedelta(minutes=3),
            )
        )


def test_evaluation_criterion_must_match_application() -> None:
    store = SQLiteExperienceStore()
    seed_context(store)
    store.save(
        criterion(
            id="crt_other",
            metric_key="payment_authorized",
            name="Payment is authorized",
        )
    )
    applied = store.save(application(id="app_checkout"))
    observed = store.save(observation(id="obs_checkout"))

    with pytest.raises(ContractReferenceError, match="criterion must match"):
        store.save(
            CriterionEvaluation(
                criterion_id="crt_other",
                lesson_application_ids=[applied.id],
                observation_ids=[observed.id],
                result=CriterionResult.MET,
                evaluator_version="comparison-v1",
                evaluated_at=NOW + timedelta(minutes=6),
            )
        )


def test_evaluation_cannot_predate_observation() -> None:
    store = SQLiteExperienceStore()
    seed_context(store)
    applied = store.save(application(id="app_checkout"))
    observed = store.save(observation(id="obs_checkout"))

    with pytest.raises(TemporalContractError, match="must not predate"):
        store.save(
            CriterionEvaluation(
                criterion_id="crt_checkout",
                lesson_application_ids=[applied.id],
                observation_ids=[observed.id],
                result=CriterionResult.MET,
                evaluator_version="comparison-v1",
                evaluated_at=NOW + timedelta(minutes=4),
            )
        )


def test_indeterminate_requires_reason_but_not_observation() -> None:
    with pytest.raises(ValidationError, match="requires reason codes"):
        CriterionEvaluation(
            criterion_id="crt_checkout",
            lesson_application_ids=["app_checkout"],
            result=CriterionResult.INDETERMINATE,
            evaluator_version="comparison-v1",
        )

    store = SQLiteExperienceStore()
    seed_context(store)
    applied = store.save(application(id="app_checkout"))
    evaluated = store.save(
        CriterionEvaluation(
            criterion_id="crt_checkout",
            lesson_application_ids=[applied.id],
            result=CriterionResult.INDETERMINATE,
            reason_codes=[EvaluationReasonCode.MISSING_OBSERVATION],
            evaluator_version="comparison-v1",
            evaluated_at=NOW + timedelta(minutes=6),
        )
    )
    assert evaluated.result is CriterionResult.INDETERMINATE


def test_idempotent_application_replay_returns_existing_record() -> None:
    store = SQLiteExperienceStore()
    seed_context(store)
    original = store.save(application(id="app_original"))
    replay = application(id="app_replay", created_at=NOW + timedelta(minutes=10))

    assert store.save(replay) == original
    assert store.list("lesson_application") == [original]


def test_idempotency_key_conflict_fails_closed() -> None:
    store = SQLiteExperienceStore()
    seed_context(store)
    store.save(application(id="app_original"))

    with pytest.raises(IdempotencyConflictError, match="different application payload"):
        store.save(application(id="app_conflict", actor_id="agent_other"))


def test_canonical_serialization_normalizes_order() -> None:
    left = OutcomeObservation(
        id="obs_order",
        lesson_application_ids=["app_b", "app_a", "app_a"],
        action_execution_id="act_checkout",
        observed_at=NOW,
        values=[
            ObservationValue(key="latency_ms", value=120, unit="ms"),
            ObservationValue(key="checkout_completed", value=True),
        ],
        source_event_ids=["evt_b", "evt_a"],
        evidence_ids=["evd_b", "evd_a"],
        collection_policy_version="collection-v1",
        created_at=NOW,
    )
    right = OutcomeObservation(
        id="obs_order",
        lesson_application_ids=["app_a", "app_b"],
        action_execution_id="act_checkout",
        observed_at=NOW,
        values=[
            ObservationValue(key="checkout_completed", value=True),
            ObservationValue(key="latency_ms", value=120, unit="ms"),
        ],
        source_event_ids=["evt_a", "evt_b"],
        evidence_ids=["evd_a", "evd_b"],
        collection_policy_version="collection-v1",
        created_at=NOW,
    )

    assert left.canonical_json() == right.canonical_json()
