from datetime import UTC, datetime

from osoznanie.action_dispatcher import ToolExecutionContext, ToolExecutionKind
from osoznanie.models import Outcome, OutcomeStatus
from osoznanie.playwright_adapter import PlaywrightQAAdapter
from osoznanie.playwright_runner import (
    BrowserCheckCode,
    BrowserCheckEvidence,
    PlaywrightCheckInput,
)
from osoznanie.storage import SQLiteExperienceStore


T0 = datetime(2026, 6, 19, 9, 0, tzinfo=UTC)


class FakeRunner:
    def __init__(self, evidence: BrowserCheckEvidence) -> None:
        self.evidence = evidence
        self.requests = []

    def run(self, request: PlaywrightCheckInput) -> BrowserCheckEvidence:
        self.requests.append(request)
        return self.evidence


def context(key: str = "idem-playwright") -> ToolExecutionContext:
    return ToolExecutionContext(
        intent_id="act_demo",
        trace_id="dtr_demo",
        worker_id="qa-worker",
        tool_call_id="call_demo",
        idempotency_key=key,
        started_attempt_id="aat_started",
    )


def request() -> PlaywrightCheckInput:
    return PlaywrightCheckInput(
        release_id="release-42",
        target_url="https://shop.example/checkout?session=must-not-persist",
        action_selector="#checkout",
        success_selector="#confirmation",
        changed_components=["checkout-button"],
    )


def test_detected_regression_is_successful_execution_with_failure_outcome() -> None:
    store = SQLiteExperienceStore()
    runner = FakeRunner(
        BrowserCheckEvidence(
            code=BrowserCheckCode.EXPECTED_STATE_NOT_OBSERVED,
            passed=False,
            target="https://shop.example/checkout",
            duration_ms=125,
        )
    )
    adapter = PlaywrightQAAdapter(store, T0, runner)

    result = adapter.execute(request(), context())

    assert result.kind is ToolExecutionKind.SUCCEEDED
    outcome = store.get(result.outcome_id or "")
    assert isinstance(outcome, Outcome)
    assert outcome.status is OutcomeStatus.FAILURE
    assert outcome.impact["release_gate"] == "blocked"
    assert outcome.impact["check_code"] == "expected_state_not_observed"
    persisted = "\n".join(record.model_dump_json() for record in store.list())
    assert "must-not-persist" not in persisted
    assert "https://shop.example/checkout" in persisted


def test_passed_browser_check_clears_release_gate_and_is_idempotent() -> None:
    store = SQLiteExperienceStore()
    runner = FakeRunner(
        BrowserCheckEvidence(
            code=BrowserCheckCode.PASSED,
            passed=True,
            target="https://shop.example/checkout",
            duration_ms=80,
        )
    )
    adapter = PlaywrightQAAdapter(store, T0, runner)

    first = adapter.execute(request(), context("same-key"))
    second = adapter.execute(request(), context("same-key"))

    assert first == second
    outcome = store.get(first.outcome_id or "")
    assert isinstance(outcome, Outcome)
    assert outcome.status is OutcomeStatus.SUCCESS
    assert outcome.impact["release_gate"] == "clear"
    assert len(runner.requests) == 1
