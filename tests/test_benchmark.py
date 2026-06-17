from pathlib import Path

import pytest
from pydantic import ValidationError

from benchmarks.evaluate import run_benchmark, write_report
from benchmarks.fixtures import BENCHMARK_NOW, build_benchmark_cases
from benchmarks.models import ErrorSignature, StrategyName
from benchmarks.strategies import (
    NaiveKeywordStrategy,
    NoMemoryStrategy,
    OsoznanieRecallStrategy,
)


def _close_cases(cases) -> None:
    for case in cases:
        case.store.close()


def test_error_signature_uses_exact_normalized_identity() -> None:
    left = ErrorSignature(
        domain=" Quality-Assurance ",
        task_type="Checkout-Release-Validation",
        pattern_id="Desktop-Only-Validation",
    )
    same = ErrorSignature(
        domain="quality-assurance",
        task_type="checkout-release-validation",
        pattern_id="desktop-only-validation",
    )
    different_version = same.model_copy(update={"version": 2})

    assert left.key == (
        "quality-assurance",
        "checkout-release-validation",
        "desktop-only-validation",
        1,
    )
    assert left.matches(same)
    assert not left.matches(different_version)


def test_error_signature_rejects_blank_pattern() -> None:
    with pytest.raises(ValidationError):
        ErrorSignature(domain="qa", task_type="release", pattern_id=" ")


def test_strategies_do_not_receive_ground_truth_and_rank_deterministically() -> None:
    cases = build_benchmark_cases()
    try:
        for case in cases:
            no_memory = NoMemoryStrategy().rank(
                case.scenario.query,
                case.store,
                now=BENCHMARK_NOW,
            )
            naive = NaiveKeywordStrategy().rank(
                case.scenario.query,
                case.store,
                now=BENCHMARK_NOW,
            )
            osoznanie = OsoznanieRecallStrategy().rank(
                case.scenario.query,
                case.store,
                now=BENCHMARK_NOW,
            )

            relevant_id = case.scenario.relevant_lesson_ids[0]
            assert no_memory == []
            assert len(naive) == 5
            assert naive[-1].lesson_id == relevant_id
            assert naive[-1].rank == 5
            assert [item.lesson_id for item in osoznanie] == [relevant_id]
            assert osoznanie[0].rank == 1
    finally:
        _close_cases(cases)


def test_benchmark_aggregate_metrics_match_fixture_design() -> None:
    cases = build_benchmark_cases()
    try:
        report = run_benchmark(cases)
    finally:
        _close_cases(cases)

    aggregates = {item.strategy: item for item in report.aggregates}

    no_memory = aggregates[StrategyName.NO_MEMORY]
    assert no_memory.hit_rate_at_1 == 0.0
    assert no_memory.hit_rate_at_3 == 0.0
    assert no_memory.mean_reciprocal_rank == 0.0
    assert no_memory.mean_false_positive_rate == 0.0
    assert no_memory.mean_score_gap is None

    naive = aggregates[StrategyName.NAIVE_KEYWORD]
    assert naive.hit_rate_at_1 == 0.0
    assert naive.hit_rate_at_3 == 0.0
    assert naive.mean_reciprocal_rank == 0.2
    assert naive.mean_false_positive_rate == 0.8
    assert naive.mean_score_gap is not None
    assert naive.mean_score_gap < 0.0

    osoznanie = aggregates[StrategyName.OSOZNANIE_RECALL]
    assert osoznanie.hit_rate_at_1 == 1.0
    assert osoznanie.hit_rate_at_3 == 1.0
    assert osoznanie.mean_reciprocal_rank == 1.0
    assert osoznanie.mean_false_positive_rate == 0.0
    assert osoznanie.mean_score_gap is not None
    assert osoznanie.mean_score_gap > 0.9


def test_reports_are_byte_reproducible(tmp_path: Path) -> None:
    first_cases = build_benchmark_cases()
    second_cases = build_benchmark_cases()
    try:
        first = run_benchmark(first_cases)
        second = run_benchmark(second_cases)
        first_paths = write_report(first, tmp_path / "first")
        second_paths = write_report(second, tmp_path / "second")
    finally:
        _close_cases(first_cases)
        _close_cases(second_cases)

    assert first.model_dump_json() == second.model_dump_json()
    assert first_paths[0].read_bytes() == second_paths[0].read_bytes()
    assert first_paths[1].read_bytes() == second_paths[1].read_bytes()
    assert "does not measure real LLM behavioral impact" in first_paths[0].read_text()
    assert "Interpretation boundary" in first_paths[1].read_text()
