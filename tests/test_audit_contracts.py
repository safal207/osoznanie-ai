from decimal import Decimal

import pytest
from pydantic import ValidationError

from benchmarks.audit_policy import RankingPolicyRef, ranking_policy_ref_for
from benchmarks.claims import ClaimScope, PolicyKind
from benchmarks.models import StrategyName
from benchmarks.path_contracts import (
    DecisionPathReasonCode,
    DecisionPathStatus,
    classify_decision_path,
    validate_status_reason,
)
from benchmarks.simulate import run_decision_simulation
from benchmarks.simulation_fixtures import build_decision_simulation_cases
from osoznanie.recall import ProvenanceRef


def _close_cases(cases) -> None:
    for case in cases:
        case.retrieval_case.store.close()


def test_structured_claim_is_machine_readable() -> None:
    cases = build_decision_simulation_cases()
    try:
        report = run_decision_simulation(cases)
    finally:
        _close_cases(cases)

    assert report.claim.scope is ClaimScope.SYNTHETIC_FIXTURES_ONLY
    assert report.claim.fixture_count == 3
    assert report.claim.policy_kind is PolicyKind.DETERMINISTIC_REFERENCE_POLICY
    assert report.claim.disclaimer


def test_alternate_action_replaces_other_bucket() -> None:
    status, reason = classify_decision_path(
        correct=False,
        repeated_error=False,
        abstained=False,
    )

    assert status is DecisionPathStatus.ALTERNATE_ACTION
    assert reason is DecisionPathReasonCode.NON_REFERENCE_ACTION_SELECTED
    assert "other" not in {item.value for item in DecisionPathStatus}


def test_status_reason_pair_is_enforced() -> None:
    validate_status_reason(
        DecisionPathStatus.SAFE_DECISION,
        DecisionPathReasonCode.SAFE_ACTION_SELECTED,
    )
    with pytest.raises(ValueError, match="does not match status"):
        validate_status_reason(
            DecisionPathStatus.SAFE_DECISION,
            DecisionPathReasonCode.POLICY_ABSTAINED,
        )


def test_ranking_policy_registry_rejects_unknown_and_inconsistent_refs() -> None:
    recall = ranking_policy_ref_for(StrategyName.OSOZNANIE_RECALL)
    assert recall is not None
    assert recall.id == "recall-ranking-v0.2"
    assert recall.score_formula_version == "scoring-v0.1"
    assert recall.score_bucket_width == Decimal("0.000001")

    with pytest.raises(ValidationError, match="unknown ranking policy"):
        RankingPolicyRef(
            id="unknown-v99",
            score_formula_version="none",
            score_bucket_width="0.000001",
            score_order="desc",
            tie_break_fields=("lesson_id",),
        )

    with pytest.raises(ValidationError, match="does not match policy"):
        RankingPolicyRef(
            id="recall-ranking-v0.2",
            score_formula_version="scoring-v0.1",
            score_bucket_width="0.01",
            score_order="desc",
            tie_break_fields=("lesson_id",),
        )


def test_completed_trial_retains_typed_retrieval_snapshot() -> None:
    cases = build_decision_simulation_cases()
    try:
        report = run_decision_simulation(cases)
    finally:
        _close_cases(cases)

    osoznanie = [
        result
        for result in report.trial_results
        if result.strategy is StrategyName.OSOZNANIE_RECALL
    ]
    assert len(osoznanie) == 3
    for result in osoznanie:
        assert result.returned_lesson_ids == [
            item.lesson_id for item in result.returned_lessons
        ]
        assert result.returned_lessons[0].score_breakdown is not None
        assert result.returned_lessons[0].reason_codes
        assert all(
            isinstance(item, ProvenanceRef)
            for item in result.returned_lessons[0].provenance_refs
        )
