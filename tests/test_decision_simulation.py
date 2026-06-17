from pathlib import Path

import pytest

from benchmarks.models import StrategyName
from benchmarks.policies import TopActionableLessonPolicy
from benchmarks.simulate import (
    NonDeterministicPolicyError,
    evaluate_decision_trial,
    run_decision_simulation,
    write_decision_report,
)
from benchmarks.simulation_fixtures import build_decision_simulation_cases
from benchmarks.simulation_models import (
    ActionRecommendation,
    DecisionDisposition,
    DecisionExplanationCode,
    DecisionTask,
    PolicyInput,
    PolicyLesson,
    SimulatedDecision,
)
from benchmarks.strategies import NoMemoryStrategy


def _close_cases(cases) -> None:
    for case in cases:
        case.retrieval_case.store.close()


def test_policy_contract_excludes_evaluator_and_strategy_fields() -> None:
    assert set(PolicyInput.model_fields) == {"task", "lessons"}
    assert set(PolicyLesson.model_fields) == {
        "lesson_id",
        "statement",
        "rank",
        "recommendation",
    }
    forbidden = {
        "error_signature",
        "safe_action_id",
        "repeated_error_action_id",
        "relevant_lesson_ids",
        "strategy",
        "score",
        "score_gap",
        "returned_score_gap",
    }
    assert forbidden.isdisjoint(PolicyInput.model_fields)
    assert forbidden.isdisjoint(PolicyLesson.model_fields)


def test_top_policy_uses_default_only_when_no_lessons_exist() -> None:
    task = DecisionTask(
        task_id="task_1",
        domain="quality-assurance",
        task_type="release-validation",
        available_actions=["approve", "test_more"],
        default_action_id="approve",
    )

    decision = TopActionableLessonPolicy().decide(
        PolicyInput(task=task, lessons=[])
    )

    assert decision == SimulatedDecision(
        action_id="approve",
        disposition=DecisionDisposition.ACT,
        applied_lesson_ids=[],
        explanation_codes=[DecisionExplanationCode.NO_LESSON_DEFAULT],
    )


def test_top_policy_applies_first_actionable_lesson_by_rank() -> None:
    task = DecisionTask(
        task_id="task_1",
        domain="quality-assurance",
        task_type="release-validation",
        context={"risk": "high"},
        available_actions=["approve", "test_more", "escalate"],
        default_action_id="approve",
    )
    lessons = [
        PolicyLesson(
            lesson_id="les_1",
            statement="Unavailable recommendation.",
            rank=1,
            recommendation=ActionRecommendation(
                action_id="not_available",
                applicable_task_types=["release-validation"],
            ),
        ),
        PolicyLesson(
            lesson_id="les_2",
            statement="Run more tests.",
            rank=2,
            recommendation=ActionRecommendation(
                action_id="test_more",
                applicable_task_types=["release-validation"],
                required_context={"risk": "high"},
            ),
        ),
    ]

    decision = TopActionableLessonPolicy().decide(
        PolicyInput(task=task, lessons=lessons)
    )

    assert decision.action_id == "test_more"
    assert decision.applied_lesson_ids == ["les_2"]
    assert decision.explanation_codes == [
        DecisionExplanationCode.TOP_ACTIONABLE_LESSON
    ]


def test_top_policy_abstains_when_lessons_are_not_actionable() -> None:
    task = DecisionTask(
        task_id="task_1",
        domain="quality-assurance",
        task_type="release-validation",
        available_actions=["approve", "test_more"],
        default_action_id="approve",
    )
    lesson = PolicyLesson(
        lesson_id="les_1",
        statement="Escalate when needed.",
        rank=1,
        recommendation=ActionRecommendation(action_id="escalate"),
    )

    decision = TopActionableLessonPolicy().decide(
        PolicyInput(task=task, lessons=[lesson])
    )

    assert decision.disposition is DecisionDisposition.ABSTAIN
    assert decision.action_id is None
    assert decision.applied_lesson_ids == []


def test_simulation_metrics_match_fixture_design() -> None:
    cases = build_decision_simulation_cases()
    try:
        report = run_decision_simulation(cases)
    finally:
        _close_cases(cases)

    aggregates = {item.strategy: item for item in report.aggregates}

    no_memory = aggregates[StrategyName.NO_MEMORY]
    assert no_memory.correct_decision_rate == 0.0
    assert no_memory.repeated_error_rate == 1.0
    assert no_memory.lesson_application_rate == 0.0
    assert no_memory.abstention_rate == 0.0
    assert no_memory.policy_coverage == 1.0

    naive = aggregates[StrategyName.NAIVE_KEYWORD]
    assert naive.correct_decision_rate == 0.0
    assert naive.repeated_error_rate == 1.0
    assert naive.lesson_application_rate == 1.0
    assert naive.abstention_rate == 0.0
    assert naive.policy_coverage == 1.0

    osoznanie = aggregates[StrategyName.OSOZNANIE_RECALL]
    assert osoznanie.correct_decision_rate == 1.0
    assert osoznanie.repeated_error_rate == 0.0
    assert osoznanie.lesson_application_rate == 1.0
    assert osoznanie.abstention_rate == 0.0
    assert osoznanie.policy_coverage == 1.0
    assert report.deterministic is True


def test_trial_results_apply_only_runtime_visible_retrieval_output() -> None:
    cases = build_decision_simulation_cases()
    try:
        report = run_decision_simulation(cases)
    finally:
        _close_cases(cases)

    for result in report.trial_results:
        if result.strategy is StrategyName.OSOZNANIE_RECALL:
            assert result.returned_lesson_count == 1
            assert result.correct is True
            assert result.repeated_error is False
        elif result.strategy is StrategyName.NAIVE_KEYWORD:
            assert result.returned_lesson_count == 5
            assert result.correct is False
            assert result.repeated_error is True
        else:
            assert result.returned_lesson_count == 0
            assert result.correct is False
            assert result.repeated_error is True


class AlternatingPolicy:
    name = "alternating_policy"

    def __init__(self) -> None:
        self.calls = 0

    def decide(self, policy_input: PolicyInput) -> SimulatedDecision:
        self.calls += 1
        if self.calls % 2:
            return SimulatedDecision(
                action_id=policy_input.task.default_action_id,
                disposition=DecisionDisposition.ACT,
                applied_lesson_ids=[],
                explanation_codes=[DecisionExplanationCode.NO_LESSON_DEFAULT],
            )
        return SimulatedDecision(
            action_id=None,
            disposition=DecisionDisposition.ABSTAIN,
            applied_lesson_ids=[],
            explanation_codes=[DecisionExplanationCode.LESSON_NOT_ACTIONABLE],
        )


def test_nondeterministic_policy_is_a_hard_failure() -> None:
    cases = build_decision_simulation_cases()
    try:
        with pytest.raises(NonDeterministicPolicyError):
            evaluate_decision_trial(
                cases[0],
                NoMemoryStrategy(),
                AlternatingPolicy(),
            )
    finally:
        _close_cases(cases)


def test_decision_reports_are_byte_reproducible(tmp_path: Path) -> None:
    first_cases = build_decision_simulation_cases()
    second_cases = build_decision_simulation_cases()
    try:
        first = run_decision_simulation(first_cases)
        second = run_decision_simulation(second_cases)
        first_paths = write_decision_report(first, tmp_path / "first")
        second_paths = write_decision_report(second, tmp_path / "second")
    finally:
        _close_cases(first_cases)
        _close_cases(second_cases)

    assert first.model_dump_json() == second.model_dump_json()
    assert first_paths[0].read_bytes() == second_paths[0].read_bytes()
    assert first_paths[1].read_bytes() == second_paths[1].read_bytes()
    assert "does not measure real LLM behavioral impact" in first_paths[0].read_text()
    assert "Interpretation boundary" in first_paths[1].read_text()
