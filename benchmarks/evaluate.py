"""Metric evaluation and deterministic report rendering."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from statistics import fmean

from .claims import PolicyKind, build_synthetic_claim
from .fixtures import BENCHMARK_NOW, BenchmarkCase
from .models import (
    AggregateMetrics,
    BenchmarkReport,
    ScenarioMetrics,
    StrategyName,
)
from .strategies import DEFAULT_STRATEGIES, RetrievalStrategy, execute_strategy

BENCHMARK_VERSION = "retrieval-quality-v0.3"


def _round(value: float) -> float:
    return round(value, 6)


def evaluate_case(
    case: BenchmarkCase,
    strategy: RetrievalStrategy,
) -> ScenarioMetrics:
    execution = execute_strategy(
        strategy,
        case.scenario.query,
        case.store,
        now=BENCHMARK_NOW,
    )
    snapshots = execution.lessons
    relevant_ids = set(case.scenario.relevant_lesson_ids)
    decoy_ids = set(case.scenario.decoy_lesson_ids)

    relevant_items = [item for item in snapshots if item.lesson_id in relevant_ids]
    false_discoveries = [item for item in snapshots if item.lesson_id not in relevant_ids]
    returned_decoys = [item for item in snapshots if item.lesson_id in decoy_ids]

    relevant_rank = min((item.rank for item in relevant_items), default=None)
    reciprocal_rank = 0.0 if relevant_rank is None else 1.0 / relevant_rank
    false_discovery_rate = (
        0.0 if not snapshots else len(false_discoveries) / len(snapshots)
    )
    decoy_selection_rate = (
        0.0 if not decoy_ids else len(returned_decoys) / len(decoy_ids)
    )

    returned_score_gap: float | None = None
    if relevant_items and returned_decoys:
        best_relevant = max(item.score for item in relevant_items)
        best_decoy = max(item.score for item in returned_decoys)
        returned_score_gap = best_relevant - best_decoy

    return ScenarioMetrics(
        scenario_id=case.scenario.scenario_id,
        strategy=strategy.name,
        returned_count=len(snapshots),
        relevant_rank=relevant_rank,
        hit_at_1=relevant_rank == 1,
        hit_at_3=relevant_rank is not None and relevant_rank <= 3,
        reciprocal_rank=_round(reciprocal_rank),
        false_discovery_rate=_round(false_discovery_rate),
        decoy_selection_rate=_round(decoy_selection_rate),
        returned_score_gap=(
            None if returned_score_gap is None else _round(returned_score_gap)
        ),
        ranked_lessons=[item.public_view() for item in snapshots],
    )


def _aggregate(
    strategy: StrategyName,
    results: list[ScenarioMetrics],
) -> AggregateMetrics:
    returned_score_gaps = [
        item.returned_score_gap
        for item in results
        if item.returned_score_gap is not None
    ]
    return AggregateMetrics(
        strategy=strategy,
        scenario_count=len(results),
        hit_rate_at_1=_round(fmean(float(item.hit_at_1) for item in results)),
        hit_rate_at_3=_round(fmean(float(item.hit_at_3) for item in results)),
        mean_reciprocal_rank=_round(fmean(item.reciprocal_rank for item in results)),
        mean_false_discovery_rate=_round(
            fmean(item.false_discovery_rate for item in results)
        ),
        mean_decoy_selection_rate=_round(
            fmean(item.decoy_selection_rate for item in results)
        ),
        mean_returned_score_gap=(
            _round(fmean(returned_score_gaps)) if returned_score_gaps else None
        ),
    )


def run_benchmark(
    cases: list[BenchmarkCase],
    strategies: tuple[RetrievalStrategy, ...] = DEFAULT_STRATEGIES,
) -> BenchmarkReport:
    scenario_results: list[ScenarioMetrics] = []
    grouped: dict[StrategyName, list[ScenarioMetrics]] = defaultdict(list)

    for strategy in strategies:
        for case in cases:
            result = evaluate_case(case, strategy)
            scenario_results.append(result)
            grouped[strategy.name].append(result)

    aggregates = [
        _aggregate(strategy.name, grouped[strategy.name])
        for strategy in strategies
    ]
    return BenchmarkReport(
        benchmark_version=BENCHMARK_VERSION,
        evaluated_at=BENCHMARK_NOW,
        claim=build_synthetic_claim(
            len(cases),
            PolicyKind.DETERMINISTIC_RETRIEVAL_EVALUATOR,
        ),
        scenario_results=scenario_results,
        aggregates=aggregates,
    )


def render_markdown(report: BenchmarkReport) -> str:
    lines = [
        "# Osoznanie Retrieval Quality Benchmark",
        "",
        f"**Version:** `{report.benchmark_version}`  ",
        f"**Evaluation time:** `{report.evaluated_at.isoformat()}`  ",
        f"**Fixture count:** `{report.claim.fixture_count}`",
        "",
        "## ⚠ Synthetic-fixture limitation",
        "",
        f"> {report.claim.disclaimer}",
        "",
        "## Aggregate results",
        "",
        (
            "| Strategy | Hit@1 | Hit@3 | MRR | FDR | Decoy selection rate | "
            "Returned score gap |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report.aggregates:
        gap = (
            "—"
            if item.mean_returned_score_gap is None
            else f"{item.mean_returned_score_gap:.6f}"
        )
        lines.append(
            f"| `{item.strategy.value}` | {item.hit_rate_at_1:.6f} | "
            f"{item.hit_rate_at_3:.6f} | {item.mean_reciprocal_rank:.6f} | "
            f"{item.mean_false_discovery_rate:.6f} | "
            f"{item.mean_decoy_selection_rate:.6f} | {gap} |"
        )

    lines.extend(
        [
            "",
            "## Scenario results",
            "",
            (
                "| Scenario | Strategy | Relevant rank | Returned | FDR | "
                "Decoy selection rate | Returned score gap |"
            ),
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for item in report.scenario_results:
        rank = "—" if item.relevant_rank is None else str(item.relevant_rank)
        gap = (
            "—"
            if item.returned_score_gap is None
            else f"{item.returned_score_gap:.6f}"
        )
        lines.append(
            f"| `{item.scenario_id}` | `{item.strategy.value}` | {rank} | "
            f"{item.returned_count} | {item.false_discovery_rate:.6f} | "
            f"{item.decoy_selection_rate:.6f} | {gap} |"
        )

    lines.extend(
        [
            "",
            "## Metric semantics",
            "",
            "- **FDR** = returned non-relevant lessons / all returned lessons.",
            "- **Decoy selection rate** = returned known decoys / all known decoys.",
            (
                "- **Returned score gap** is defined only when both a relevant lesson "
                "and at least one known decoy are returned."
            ),
            "",
            "## Interpretation boundary",
            "",
            (
                "A high retrieval score means the strategy ranked the fixture's known "
                "lesson well. It does not establish live-agent behavior or real-world "
                "incident reduction."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def write_report(report: BenchmarkReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "retrieval-quality.json"
    markdown_path = output_dir / "retrieval-quality.md"
    json_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path
