"""End-to-end QA release demonstrator for Osoznanie AI."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field, SecretStr

from osoznanie.access_control import (
    AccessEffect,
    AccessPolicyContent,
    AccessResource,
    AccessResourceKind,
    AuthorizationEngine,
    AuthorizationQuery,
)
from osoznanie.action_dispatcher import (
    DispatcherStatus,
    ResolvedToolInput,
    ToolExecutionContext,
    ToolExecutionResult,
)
from osoznanie.decision_trace import DecisionTrace
from osoznanie.decision_trace_store import SQLiteDecisionTraceStore
from osoznanie.memory import MemoryObject, MemoryType
from osoznanie.models import (
    Decision,
    Event,
    Hypothesis,
    Lesson,
    LessonScope,
    Outcome,
    OutcomeStatus,
    Reflection,
    ValidationStatus,
)
from osoznanie.orchestration import (
    AuditedDecisionOrchestrator,
    AuditedDecisionRequest,
    AuditedDecisionStatus,
    DecisionContext,
    DecisionProposal,
)
from osoznanie.sqlite_access_control import (
    SQLiteAccessPolicyStore,
    SQLiteAuthorizedMemoryStore,
)
from osoznanie.sqlite_action_outbox import SQLiteActionOutbox
from osoznanie.storage import DuplicateRecordError, SQLiteExperienceStore
from osoznanie.strict_action_dispatcher import StrictActionWorkerDispatcher


@dataclass(frozen=True)
class QADemoResult:
    trace_status: str
    dispatcher_status: str
    policy_memory_id: str
    behavioral_memory_id: str
    trace_id: str
    intent_id: str
    started_attempt_id: str
    terminal_attempt_id: str
    outcome_id: str
    reflection_id: str
    lesson_id: str
    release_gate: str
    provider_token_persisted: bool


class QAReleaseInput(BaseModel):
    release_id: str = Field(min_length=1)
    browser: str = Field(min_length=1)
    changed_components: list[str] = Field(min_length=1)
    provider_token: SecretStr


class SequenceClock:
    def __init__(self, *values: datetime) -> None:
        self.values = list(values)

    def __call__(self) -> datetime:
        return self.values.pop(0)


class InMemoryPayloadResolver:
    def __init__(self, payloads: dict[str, dict[str, object]]) -> None:
        self.payloads = payloads

    def resolve(self, intent) -> ResolvedToolInput:
        payload = self.payloads[intent.input_hash]
        return ResolvedToolInput(
            payload=payload,
            input_hash=canonical_input_hash(payload),
        )


class QATestRunnerAdapter:
    tool_name = "qa.test_runner"
    input_model = QAReleaseInput

    def __init__(self, store: SQLiteExperienceStore, observed_at: datetime) -> None:
        self.store = store
        self.observed_at = observed_at

    def execute(
        self,
        request: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        if not isinstance(request, QAReleaseInput):
            return ToolExecutionResult.permanent("invalid_request_model")

        suffix = hashlib.sha256(
            context.idempotency_key.encode("utf-8")
        ).hexdigest()[:16]
        outcome_id = f"out_qa_{suffix}"
        if self.store.exists(outcome_id):
            return ToolExecutionResult.succeeded(outcome_id)

        event = Event(
            id=f"evt_qa_{suffix}",
            actor_ids=[context.worker_id],
            summary=(
                f"Release {request.release_id} executed checkout regression tests "
                f"on {request.browser}."
            ),
            context={
                "release_id": request.release_id,
                "browser": request.browser,
                "changed_components": sorted(request.changed_components),
            },
            timestamp=self.observed_at,
            created_at=self.observed_at,
        )
        decision = Decision(
            id=f"dec_qa_{suffix}",
            event_id=event.id,
            agent_id=context.worker_id,
            chosen_action="release.review",
            alternatives_considered=["approve_without_regression"],
            reasoning_summary=(
                "Executed the remembered Chrome checkout regression rule before approval."
            ),
            confidence=1.0,
            created_at=self.observed_at,
        )
        outcome = Outcome(
            id=outcome_id,
            decision_id=decision.id,
            status=OutcomeStatus.FAILURE,
            summary="Checkout button regression detected; release approval blocked.",
            impact={
                "release_gate": "blocked",
                "failed_check": "checkout_button_click",
                "browser": request.browser,
                "release_id": request.release_id,
            },
            observed_at=self.observed_at,
            created_at=self.observed_at,
        )
        save_idempotent(self.store, event)
        save_idempotent(self.store, decision)
        save_idempotent(self.store, outcome)
        response_hash = canonical_input_hash(
            {
                "outcome_id": outcome.id,
                "release_gate": "blocked",
                "failed_check": "checkout_button_click",
            }
        )
        return ToolExecutionResult.succeeded(
            outcome.id,
            response_hash=response_hash,
        )


def canonical_input_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def save_idempotent(store: SQLiteExperienceStore, record):
    try:
        return store.save(record)
    except DuplicateRecordError:
        existing = store.get(record.id)
        if existing != record:
            raise
        return existing


def run_qa_release_demo(
    database_path: str = ":memory:",
    *,
    base_time: datetime | None = None,
) -> tuple[SQLiteExperienceStore, QADemoResult]:
    store = SQLiteExperienceStore(database_path)
    seeded_at = (base_time or datetime.now(UTC)).astimezone(UTC)
    suffix = hashlib.sha256(seeded_at.isoformat().encode("utf-8")).hexdigest()[:12]
    behavioral_key = f"qa.rules.checkout.chrome.{suffix}"

    rule_event = Event(
        id=f"evt_rule_{suffix}",
        actor_ids=["qa-lead"],
        summary="A Chrome checkout regression previously escaped without device testing.",
        context={"browser": "chrome", "area": "checkout"},
        timestamp=seeded_at,
        created_at=seeded_at,
    )
    policy_event = Event(
        id=f"evt_policy_{suffix}",
        actor_ids=["security-admin"],
        summary="QA agent may read the checkout regression rule for release review.",
        timestamp=seeded_at,
        created_at=seeded_at,
    )
    save_idempotent(store, rule_event)
    save_idempotent(store, policy_event)

    behavioral_memory = MemoryObject(
        id=f"mem_rule_{suffix}",
        memory_key=behavioral_key,
        memory_type=MemoryType.BEHAVIORAL_RULE,
        content={
            "statement": (
                "When checkout frontend code changes, run Chrome checkout regression "
                "before approving the release."
            ),
            "target_check": "checkout_button_click",
        },
        source_event_ids=[rule_event.id],
        confidence=0.98,
        importance=1.0,
        valid_from=seeded_at,
        created_at=seeded_at,
        updated_at=seeded_at,
    )
    policy_content = AccessPolicyContent(
        subject_id="qa-agent",
        action="release.review",
        resource=AccessResource(
            kind=AccessResourceKind.EXACT_KEY,
            value=behavioral_key,
        ),
        effect=AccessEffect.ALLOW,
    )
    policy_memory = MemoryObject(
        id=f"mem_policy_{suffix}",
        memory_key=f"policy.qa.release-review.{suffix}",
        memory_type=MemoryType.ACCESS_POLICY,
        content=policy_content.model_dump(mode="json"),
        source_event_ids=[policy_event.id],
        confidence=1.0,
        importance=1.0,
        valid_from=seeded_at,
        created_at=seeded_at,
        updated_at=seeded_at,
    )
    save_idempotent(store, behavioral_memory)
    save_idempotent(store, policy_memory)

    query_time = max(
        seeded_at + timedelta(minutes=1),
        datetime.now(UTC) + timedelta(seconds=2),
    )
    decision_time = query_time + timedelta(minutes=1)
    worker_started_at = decision_time + timedelta(minutes=1)
    worker_finished_at = worker_started_at + timedelta(minutes=1)

    protected_payload: dict[str, object] = {
        "release_id": f"release-{suffix}",
        "browser": "chrome",
        "changed_components": ["checkout-button", "frontend-bundle"],
        "provider_token": "demo-provider-token-must-never-persist",
    }
    input_hash = canonical_input_hash(protected_payload)

    trace_store = SQLiteDecisionTraceStore(store)
    outbox = SQLiteActionOutbox(store, trace_store)
    orchestrator = AuditedDecisionOrchestrator(
        authorization=AuthorizationEngine(SQLiteAccessPolicyStore(store)),
        memory_store=SQLiteAuthorizedMemoryStore(store),
        trace_store=trace_store,
        outcome_store=store,
    )
    request = AuditedDecisionRequest(
        authorization_query=AuthorizationQuery(
            requester_id="qa-agent",
            action="release.review",
            as_of=query_time,
            known_at=query_time,
            memory_keys=[behavioral_key],
        ),
        agent_id="qa-agent",
        decision_at=decision_time,
    )
    captured_intent = {}

    def decide(context: DecisionContext) -> DecisionProposal:
        remembered_keys = {
            entry.memory.memory_key for entry in context.memory_view.entries
        }
        if behavioral_key not in remembered_keys:
            raise RuntimeError("authorized QA rule was not projected")
        return DecisionProposal(
            action="release.review",
            alternatives_considered=["approve_without_regression"],
            reason_codes=["validated_prior_checkout_escape"],
            tool_name=QATestRunnerAdapter.tool_name,
            tool_call_id=f"call_qa_{suffix}",
            input_hash=input_hash,
        )

    def enqueue(proposal: DecisionProposal, trace: DecisionTrace):
        captured_intent["intent"] = outbox.enqueue(trace, proposal)
        return None

    trace_result = orchestrator.run(request, decide, enqueue)
    if trace_result.status is not AuditedDecisionStatus.ACTION_COMPLETED:
        raise RuntimeError(f"unexpected orchestration status: {trace_result.status}")
    intent = captured_intent["intent"]

    dispatcher = StrictActionWorkerDispatcher(
        store,
        "qa-worker",
        InMemoryPayloadResolver({input_hash: protected_payload}),
        [QATestRunnerAdapter(store, worker_finished_at)],
        lease_for=timedelta(minutes=10),
        clock=SequenceClock(worker_started_at, worker_finished_at),
        outbox=outbox,
    )
    dispatch_result = dispatcher.dispatch_once()
    if dispatch_result.status is not DispatcherStatus.COMPLETED:
        raise RuntimeError(f"unexpected dispatcher status: {dispatch_result.status}")

    outcome = store.get(dispatch_result.outcome_id or "")
    if not isinstance(outcome, Outcome):
        raise RuntimeError("test runner did not persist an Outcome")
    learning_time = worker_finished_at + timedelta(minutes=1)
    learning_suffix = outcome.id.removeprefix("out_qa_")
    reflection = Reflection(
        id=f"ref_qa_{learning_suffix}",
        source_ids=[outcome.id],
        hypotheses=[
            Hypothesis(
                statement=(
                    "Checkout frontend changes remain a high-risk Chrome regression "
                    "surface and require an explicit click-path check."
                ),
                confidence=0.97,
            )
        ],
        limitations=["Synthetic deterministic test-runner used by the demonstrator."],
        validation_status=ValidationStatus.MACHINE_REVIEWED,
        created_at=learning_time,
    )
    lesson = Lesson(
        id=f"les_qa_{learning_suffix}",
        statement=(
            "Before release approval, run the Chrome checkout click-path regression "
            "whenever checkout frontend components change."
        ),
        scope=LessonScope(
            domain="qa",
            task_types=["release.review"],
            tags=["checkout", "chrome", "regression"],
        ),
        source_reflection_ids=[reflection.id],
        confidence=0.97,
        validation_status=ValidationStatus.ACTIVE,
        effective_from=learning_time,
        created_at=learning_time,
    )
    save_idempotent(store, reflection)
    save_idempotent(store, lesson)

    provider_token_persisted = persistent_text_contains(
        store,
        "demo-provider-token-must-never-persist",
    )
    return store, QADemoResult(
        trace_status=trace_result.status.value,
        dispatcher_status=dispatch_result.status.value,
        policy_memory_id=policy_memory.id,
        behavioral_memory_id=behavioral_memory.id,
        trace_id=trace_result.initial_trace_id or "",
        intent_id=intent.id,
        started_attempt_id=dispatch_result.started_attempt_id or "",
        terminal_attempt_id=dispatch_result.terminal_attempt_id or "",
        outcome_id=outcome.id,
        reflection_id=reflection.id,
        lesson_id=lesson.id,
        release_gate=str(outcome.impact["release_gate"]),
        provider_token_persisted=provider_token_persisted,
    )


def persistent_text_contains(store: SQLiteExperienceStore, needle: str) -> bool:
    with store._connect() as connection:
        record_rows = connection.execute("SELECT payload FROM records").fetchall()
        outbox_rows = connection.execute("SELECT * FROM action_outbox").fetchall()
    text = "\n".join(
        [row["payload"] for row in record_rows]
        + [str(dict(row)) for row in outbox_rows]
    )
    return needle in text


def main() -> None:
    store, result = run_qa_release_demo()
    try:
        print(json.dumps(asdict(result), indent=2, sort_keys=True))
    finally:
        store.close()


if __name__ == "__main__":
    main()
