"""Evaluation and report rendering for deterministic decision simulation."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from statistics import fmean

from osoznanie.models import Lesson
from osoznanie.storage import RecordNotFoundError

from .fixtures import BENCHMARK_NOW
from .models import RankedLesson, StrategyName
from .policies import DecisionPolicy, TopActionableLessonPolicy
from .simulation_fixtures import DecisionSimulationCase
from .simulation_models import (
    DecisionAggregateMetrics,
    DecisionDisposition,
    DecisionSimulationReport,
    DecisionTrialResult,
    PolicyInput,
    PolicyLesson,
    SimulatedDecision,
)
from .strategies import DEFAULT_STRATEGIES, RetrievalStrategy

SIMULATION_VERSION = "decision-policy-v0.1"
CLAIM = (
    "This report measures a deterministic policy simulation over synthetic "
    "fixtures; it does not measure real LLM behavioral impact."
)


class SimulationInputError(RuntimeError):
    """Raised when retrieval output cannot be adapted into policy input."""


class NonDeterministicPolicyError(RuntimeError):
    """Raised when identical policy inputs produce different decisions."""


def _round(value: float) -> float:
    return round(value, 6)


def _policy_lessons(
    case: DecisionSimulationCase,
    ranked_lessons: list[RankedLesson],
) -> list[PolicyLesson]:
    lessons: list[PolicyLesson] = []
    for ranked in sorted(ranked_lessons, key=lambda item: item.rank):
        try:
            record = case.retrieval_case.store.get(ranked.lesson_id)
        except RecordNotFoundError as error:
            raise SimulationInputError(
                f"retrieval returned missing lesson: {ranked.lesson_id}"
            ) from error
        if not isinstance(record, Lesson):
            raise SimulationInputError(
                f"retrieval returned non-lesson record: {ranked.lesson_id}"
            )
        lessons.append(
            PolicyLesson(
                lesson_id=record.id,
                statement=record.statement,
                rank=ranked.rank,
                recommendation=case.scenario.recommendations.get(record.id),
            )
        )
    return lessons


def _run_deterministically(
    policy: DecisionPolicy,
    policy_input: PolicyInput,
) -> SimulatedDecision:
    first = policy.decide(policy_input)
    second = policy.decide(policy_input)
    if first.model_dump_json() != second.model_dump_json():
        raise NonDeterministicPolicyError(
            f"policy {policy.name!r} produced inconsistent decisions"
        )
    return first


def evaluate_decision_trial(
    case: DecisionSimulationCase,
    strategy: RetrievalStrategy,
    policy: DecisionPolicy,
) -> DecisionTrialResult:
    ranked = strategy.rank(
        case.retrieval_case.scenario.query,
        case.retrieval_case.store,
        now=BENCHMARK_NOW,
    )
    policy_input = PolicyInput(
        task=case.scenario.task,
        lessons=_policy_lessons(case, ranked),
    )
    decision = _run_deterministically(policy, policy_input)

    return DecisionTrialResult(
        scenario_id=case.scenario.scenario_id,
        strategy=strategy.name,
        returned_lesson_count=len(policy_input.lessons),
        decision=decision,
        correct=decision.action_id == case.scenario.safe_action_id,
        repeated_error=(
            decision.action_id == case.scenario.repeated_error_action_id
        ),
        lesson_applied=bool(decision.applied_lesson_ids),
        abstained=decision.disposition is DecisionDisposition.ABSTAIN,
    )


def _aggregate(
    strategy: StrategyName,
    results: list[DecisionTrialResult],
) -> DecisionAggregateMetrics:
    with_lessons = [item for item in results if item.returned_lesson_count > 0]
    application_rate = (
        fmean(float(item.lesson_applied) for item in with_lessons)
        if with_lessons
        else 0.0
    )
    return DecisionAggregateMetrics(
        strategy=strategy,
        trial_count=len(results),
        correct_decision_rate=_round(
            fmean(float(item.correct) for item in results)
        ),
        repeated_error_rate=_round(
            fmean(float(item.repeated_error) for item in results)
        ),
        lesson_application_rate=_round(application_rate),
        abstention_rate=_round(
            fmean(float(item.abstained) for item in results)
        ),
        policy_coverage=_round(
            fmean(float(not item.abstained) for item in results)
        ),
    )


def run_decision_simulation(
    cases: list[DecisionSimulationCase],
    strategies: tuple[RetrievalStrategy, ...] = DEFAULT_STRATEGIES,
    policy: DecisionPolicy | None = None,
) -> DecisionSimulationReport:
    effective_policy = policy or TopActionableLessonPolicy()
    trial_results: list[DecisionTrialResult] = []
    grouped: dict[StrategyName, list[DecisionTrialResult]] = defaultdict(list)

    for strategy in strategies:
        for case in cases:
            result = evaluate_decision_trial(case, strategy, effective_policy)
            trial_results.append(result)
            grouped[strategy.name].append(result)

    aggregates = [
        _aggregate(strategy.name, grouped[strategy.name])
        for strategy in strategies
    ]
    return DecisionSimulationReport(
        simulation_version=SIMULATION_VERSION,
        evaluated_at=BENCHMARK_NOW,
        claim=CLAIM,
        policy_name=effective_policy.name,
        deterministic=True,
        trial_results=trial_results,
        aggregates=aggregates,
    )


def render_decision_markdown(report: DecisionSimulationReport) -> str:
    lines = [
        "# Osoznanie Deterministic Decision Simulation",
        "",
        f"**Version:** `{report.simulation_version}`  ",
        f"**Policy:** `{report.policy_name}`  ",
        f"**Evaluation time:** `{report.evaluated_at.isoformat()}`  ",
        f"**Deterministic invariant:** `{str(report.deterministic).lower()}`",
        "",
        f"> {report.claim}",
        "",
        "## Aggregate results",
        "",
        (
            "| Strategy | Correct decisions | Repeated errors | Lesson application | "
            "Abstention | Coverage |"
        ),
        "|---|---:|---:|---:|---:|---:|",
    ]
    for item in report.aggregates:
        lines.append(
            f"| `{item.strategy.value}` | {item.correct_decision_rate:.6f} | "
            f"{item.repeated_error_rate:.6f} | "
            f"{item.lesson_application_rate:.6f} | "
            f"{item.abstention_rate:.6f} | {item.policy_coverage:.6f} |"
        )

    lines.extend(
        [
            "",
            "## Trial results",
            "",
            (
                "| Scenario | Strategy | Lessons | Action | Correct | "
                "Repeated error | Abstained |"
            ),
            "|---|---|---:|---|---:|---:|---:|",
        ]
    )
    for item in report.trial_results:
        action = item.decision.action_id or "—"
        lines.append(
            f"| `{item.scenario_id}` | `{item.strategy.value}` | "
            f"{item.returned_lesson_count} | `{action}` | "
            f"{str(item.correct).lower()} | "
            f"{str(item.repeated_error).lower()} | "
            f"{str(item.abstained).lower()} |"
        )

    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            (
                "The result shows how retrieval output changes one transparent, "
                "deterministic reference policy. It does not prove that a real language "
                "model would apply the same lesson or choose the same action."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def write_decision_report(
    report: DecisionSimulationReport,
    output_dir: Path,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "decision-simulation.json"
    markdown_path = output_dir / "decision-simulation.md"
    json_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_decision_markdown(report), encoding="utf-8")
    return json_path, markdown_path
