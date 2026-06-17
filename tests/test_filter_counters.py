import pytest
from pydantic import ValidationError

from osoznanie.recall import RecallEngine

from benchmarks.audit_paths import build_audit_path_bundle
from benchmarks.filter_contracts import (
    AuditedCount,
    CountVisibility,
    FilterSummary,
)
from benchmarks.fixtures import BENCHMARK_NOW, build_benchmark_cases
from benchmarks.models import StrategyName
from benchmarks.simulate import run_decision_simulation
from benchmarks.simulation_fixtures import build_decision_simulation_cases
from benchmarks.strategies import OsoznanieRecallStrategy, execute_strategy


def _close_benchmark_cases(cases) -> None:
    for case in cases:
        case.store.close()


def _close_decision_cases(cases) -> None:
    for case in cases:
        case.retrieval_case.store.close()


def test_audited_count_enforces_visibility_invariants() -> None:
    assert AuditedCount.disclosed(0) == AuditedCount(
        visibility=CountVisibility.DISCLOSED,
        value=0,
    )
    assert AuditedCount.redacted() == AuditedCount(
        visibility=CountVisibility.REDACTED,
        value=None,
    )

    with pytest.raises(ValidationError, match="non-null value"):
        AuditedCount(visibility=CountVisibility.DISCLOSED, value=None)
    with pytest.raises(ValidationError, match="null value"):
        AuditedCount(visibility=CountVisibility.REDACTED, value=1)
    with pytest.raises(ValidationError):
        AuditedCount.disclosed(-1)


def test_existing_fixtures_produce_exact_first_exclusion_counts() -> None:
    cases = build_benchmark_cases()
    try:
        for case in cases:
            execution = execute_strategy(
                OsoznanieRecallStrategy(),
                case.scenario.query,
                case.store,
                now=BENCHMARK_NOW,
            )
            counts = execution.filter_counts
            assert counts is not None
            assert counts.validation_rejected == 1
            assert counts.not_yet_effective == 0
            assert counts.expired == 1
            assert counts.domain_mismatch == 1
            assert counts.insufficient_scope == 0
            assert counts.access_denied == 1
            assert counts.below_risk_threshold == 0
            assert len(execution.lessons) == 1
    finally:
        _close_benchmark_cases(cases)


def test_recall_api_matches_diagnostic_results() -> None:
    cases = build_benchmark_cases()
    try:
        case = cases[0]
        engine = RecallEngine(case.store)
        plain = engine.recall(case.scenario.query, now=BENCHMARK_NOW)
        diagnostic = engine.recall_with_diagnostics(
            case.scenario.query,
            now=BENCHMARK_NOW,
        )
        assert plain == diagnostic.results
    finally:
        _close_benchmark_cases(cases)


def test_public_and_restricted_summaries_apply_visibility_without_ids() -> None:
    cases = build_decision_simulation_cases()
    try:
        report = run_decision_simulation(cases)
        bundle = build_audit_path_bundle(cases, report)
    finally:
        _close_decision_cases(cases)

    public_by_key = {
        (graph.scenario_id, graph.strategy): graph
        for graph in bundle.public_graphs
    }
    audit_by_key = {
        (audit.scenario_id, audit.strategy): audit for audit in bundle.audits
    }

    for case in cases:
        scenario_id = case.scenario.scenario_id
        key = (scenario_id, StrategyName.OSOZNANIE_RECALL)
        public = public_by_key[key]
        audit = audit_by_key[key]

        assert public.filter_summary is not None
        assert public.filter_summary.access_denied == AuditedCount.redacted()
        assert public.filter_summary.validation_rejected.value == 1
        assert public.filter_summary.expired.value == 1
        assert public.filter_summary.domain_mismatch.value == 1

        assert audit.filter_summary is not None
        assert audit.filter_summary.access_denied == AuditedCount.disclosed(1)

        filtered_id = f"les_{scenario_id}_access_denied"
        assert filtered_id not in public.model_dump_json()
        assert filtered_id not in audit.model_dump_json()


def test_unstructured_strategies_use_null_filter_summary() -> None:
    cases = build_decision_simulation_cases()
    try:
        report = run_decision_simulation(cases)
        bundle = build_audit_path_bundle(cases, report)
    finally:
        _close_decision_cases(cases)

    for graph in bundle.public_graphs:
        if graph.strategy is not StrategyName.OSOZNANIE_RECALL:
            assert graph.filter_summary is None
    for audit in bundle.audits:
        if audit.strategy is not StrategyName.OSOZNANIE_RECALL:
            assert audit.filter_summary is None


def test_redacted_does_not_claim_that_hidden_candidates_exist() -> None:
    summary = FilterSummary.public(
        RecallEngine(build_benchmark_cases()[0].store)
        .recall_with_diagnostics(
            build_benchmark_cases()[0].scenario.query,
            now=BENCHMARK_NOW,
        )
        .filter_counts
    )
    assert summary.access_denied.visibility is CountVisibility.REDACTED
    assert summary.access_denied.value is None
