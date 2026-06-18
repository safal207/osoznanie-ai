from datetime import UTC, datetime

from examples.qa_release_demo import run_qa_release_demo
from osoznanie.action_attempt import ActionAttemptStatus
from osoznanie.action_attempt_store import SQLiteActionAttemptStore
from osoznanie.action_outbox import ActionIntentStatus
from osoznanie.decision_trace import DecisionTrace
from osoznanie.models import (
    Lesson,
    Outcome,
    OutcomeStatus,
    Reflection,
    ValidationStatus,
)
from osoznanie.sqlite_action_outbox import SQLiteActionOutbox


def test_qa_release_demo_runs_full_audited_learning_loop() -> None:
    store, result = run_qa_release_demo(base_time=datetime.now(UTC))
    try:
        assert result.trace_status == "action_completed"
        assert result.dispatcher_status == "completed"
        assert result.release_gate == "blocked"
        assert result.provider_token_persisted is False

        trace = store.get(result.trace_id)
        assert isinstance(trace, DecisionTrace)
        assert trace.policy_memory_ids == [result.policy_memory_id]
        assert trace.memory_ids == [result.behavioral_memory_id]
        assert trace.tool_name == "qa.test_runner"

        outbox = SQLiteActionOutbox(store)
        intent = outbox.get(result.intent_id)
        assert intent.status is ActionIntentStatus.COMPLETED
        assert intent.outcome_id == result.outcome_id
        assert intent.lease_token is None

        attempts = SQLiteActionAttemptStore(store, outbox).list(result.intent_id)
        assert [attempt.status for attempt in attempts] == [
            ActionAttemptStatus.STARTED,
            ActionAttemptStatus.SUCCEEDED,
        ]
        assert attempts[0].id == result.started_attempt_id
        assert attempts[1].id == result.terminal_attempt_id
        assert attempts[1].outcome_id == result.outcome_id

        outcome = store.get(result.outcome_id)
        assert isinstance(outcome, Outcome)
        assert outcome.status is OutcomeStatus.FAILURE
        assert outcome.impact["release_gate"] == "blocked"
        assert outcome.impact["failed_check"] == "checkout_button_click"

        reflection = store.get(result.reflection_id)
        assert isinstance(reflection, Reflection)
        assert reflection.source_ids == [outcome.id]
        assert reflection.validation_status is ValidationStatus.MACHINE_REVIEWED

        lesson = store.get(result.lesson_id)
        assert isinstance(lesson, Lesson)
        assert lesson.source_reflection_ids == [reflection.id]
        assert lesson.validation_status is ValidationStatus.ACTIVE
        assert lesson.scope.domain == "qa"
        assert lesson.scope.task_types == ["release.review"]
        assert lesson.scope.tags == ["checkout", "chrome", "regression"]
    finally:
        store.close()
