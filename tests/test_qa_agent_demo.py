from examples.qa_agent_demo import REQUIRED_CHECKS, run_demo
from osoznanie import AuditedDecisionStatus, OutcomeStatus


def test_audited_qa_demo_closes_the_learning_loop() -> None:
    result = run_demo()

    assert result.initial_outcome_status is OutcomeStatus.FAILURE
    assert result.final_outcome_status is OutcomeStatus.SUCCESS
    assert result.pipeline_status is AuditedDecisionStatus.OUTCOME_TRACED
    assert result.checks_executed == REQUIRED_CHECKS
    assert result.trace_memory_ids == (result.lesson_memory_id,)
    assert result.trace_policy_memory_ids == (result.policy_memory_id,)
    assert result.trace_versions == (1, 2)
    assert result.initial_trace_id != result.outcome_trace_id
    assert result.final_outcome_id == "out_checkout_release_2"
    assert "browser-device matrix" in result.recalled_lesson
