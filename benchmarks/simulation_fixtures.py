"""Deterministic decision-simulation fixtures built on retrieval cases."""

from __future__ import annotations

from dataclasses import dataclass

from .fixtures import BenchmarkCase, build_benchmark_cases
from .simulation_models import (
    ActionRecommendation,
    DecisionScenario,
    DecisionTask,
)


@dataclass(frozen=True)
class DecisionSimulationCase:
    retrieval_case: BenchmarkCase
    scenario: DecisionScenario


def _recommendations(
    case: BenchmarkCase,
    *,
    safe_action_id: str,
    repeated_error_action_id: str,
    task_type: str,
    required_context: dict[str, str],
) -> dict[str, ActionRecommendation]:
    relevant_ids = set(case.scenario.relevant_lesson_ids)
    all_ids = [
        *case.scenario.relevant_lesson_ids,
        *case.scenario.decoy_lesson_ids,
    ]
    return {
        lesson_id: ActionRecommendation(
            action_id=(
                safe_action_id
                if lesson_id in relevant_ids
                else repeated_error_action_id
            ),
            applicable_task_types=[task_type],
            required_context=required_context,
        )
        for lesson_id in all_ids
    }


def _checkout_case(case: BenchmarkCase) -> DecisionSimulationCase:
    task = DecisionTask(
        task_id="task_checkout_release",
        domain="quality-assurance",
        task_type="checkout-release-validation",
        context={
            "feature": "checkout",
            "platform": "android",
            "browser": "chrome",
        },
        available_actions=[
            "approve_desktop_only",
            "run_browser_device_matrix",
            "escalate_to_human",
        ],
        default_action_id="approve_desktop_only",
    )
    recommendations = _recommendations(
        case,
        safe_action_id="run_browser_device_matrix",
        repeated_error_action_id="approve_desktop_only",
        task_type=task.task_type,
        required_context={"feature": "checkout"},
    )
    return DecisionSimulationCase(
        retrieval_case=case,
        scenario=DecisionScenario(
            scenario_id=case.scenario.scenario_id,
            task=task,
            error_signature=case.scenario.error_signature,
            safe_action_id="run_browser_device_matrix",
            repeated_error_action_id="approve_desktop_only",
            relevant_lesson_ids=case.scenario.relevant_lesson_ids,
            recommendations=recommendations,
        ),
    )


def _transfer_case(case: BenchmarkCase) -> DecisionSimulationCase:
    task = DecisionTask(
        task_id="task_transfer_timeout",
        domain="quality-assurance",
        task_type="cross-system-transfer-timeout-analysis",
        context={
            "operation": "securities-transfer",
            "status_pattern": "inconsistent",
            "timeout": "true",
        },
        available_actions=[
            "close_as_timeout",
            "reconcile_all_system_statuses",
            "escalate_to_human",
        ],
        default_action_id="close_as_timeout",
    )
    recommendations = _recommendations(
        case,
        safe_action_id="reconcile_all_system_statuses",
        repeated_error_action_id="close_as_timeout",
        task_type=task.task_type,
        required_context={"status_pattern": "inconsistent"},
    )
    return DecisionSimulationCase(
        retrieval_case=case,
        scenario=DecisionScenario(
            scenario_id=case.scenario.scenario_id,
            task=task,
            error_signature=case.scenario.error_signature,
            safe_action_id="reconcile_all_system_statuses",
            repeated_error_action_id="close_as_timeout",
            relevant_lesson_ids=case.scenario.relevant_lesson_ids,
            recommendations=recommendations,
        ),
    )


def _regression_case(case: BenchmarkCase) -> DecisionSimulationCase:
    task = DecisionTask(
        task_id="task_incident_regression",
        domain="quality-assurance",
        task_type="production-regression-planning",
        context={
            "release_type": "production",
            "prior_incident": "true",
            "checklist_state": "incomplete",
        },
        available_actions=[
            "approve_without_incident_checks",
            "run_incident_regression_suite",
            "escalate_to_human",
        ],
        default_action_id="approve_without_incident_checks",
    )
    recommendations = _recommendations(
        case,
        safe_action_id="run_incident_regression_suite",
        repeated_error_action_id="approve_without_incident_checks",
        task_type=task.task_type,
        required_context={"prior_incident": "true"},
    )
    return DecisionSimulationCase(
        retrieval_case=case,
        scenario=DecisionScenario(
            scenario_id=case.scenario.scenario_id,
            task=task,
            error_signature=case.scenario.error_signature,
            safe_action_id="run_incident_regression_suite",
            repeated_error_action_id="approve_without_incident_checks",
            relevant_lesson_ids=case.scenario.relevant_lesson_ids,
            recommendations=recommendations,
        ),
    )


def build_decision_simulation_cases() -> list[DecisionSimulationCase]:
    retrieval_cases = {
        case.scenario.scenario_id: case
        for case in build_benchmark_cases()
    }
    return [
        _checkout_case(retrieval_cases["checkout_desktop_only"]),
        _transfer_case(retrieval_cases["transfer_timeout_status_split"]),
        _regression_case(retrieval_cases["incident_regression_omission"]),
    ]
