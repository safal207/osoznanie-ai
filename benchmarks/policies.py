"""Deterministic decision policies used by the simulation benchmark."""

from __future__ import annotations

from typing import Protocol

from .simulation_models import (
    ActionRecommendation,
    DecisionDisposition,
    DecisionExplanationCode,
    DecisionTask,
    PolicyInput,
    SimulatedDecision,
)


class DecisionPolicy(Protocol):
    name: str

    def decide(self, policy_input: PolicyInput) -> SimulatedDecision: ...


def _is_actionable(
    task: DecisionTask,
    recommendation: ActionRecommendation,
) -> bool:
    if recommendation.action_id not in task.available_actions:
        return False
    if (
        recommendation.applicable_task_types
        and task.task_type not in recommendation.applicable_task_types
    ):
        return False
    return all(
        task.context.get(key) == value
        for key, value in recommendation.required_context.items()
    )


class TopActionableLessonPolicy:
    """Apply the first actionable lesson, default without lessons, else abstain."""

    name = "top_actionable_lesson_v0.1"

    def decide(self, policy_input: PolicyInput) -> SimulatedDecision:
        if not policy_input.lessons:
            return SimulatedDecision(
                action_id=policy_input.task.default_action_id,
                disposition=DecisionDisposition.ACT,
                applied_lesson_ids=[],
                explanation_codes=[DecisionExplanationCode.NO_LESSON_DEFAULT],
            )

        for lesson in policy_input.lessons:
            recommendation = lesson.recommendation
            if recommendation is None:
                continue
            if _is_actionable(policy_input.task, recommendation):
                return SimulatedDecision(
                    action_id=recommendation.action_id,
                    disposition=DecisionDisposition.ACT,
                    applied_lesson_ids=[lesson.lesson_id],
                    explanation_codes=[
                        DecisionExplanationCode.TOP_ACTIONABLE_LESSON
                    ],
                )

        return SimulatedDecision(
            action_id=None,
            disposition=DecisionDisposition.ABSTAIN,
            applied_lesson_ids=[],
            explanation_codes=[DecisionExplanationCode.LESSON_NOT_ACTIONABLE],
        )
