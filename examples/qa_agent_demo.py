"""Executable end-to-end demonstration of Osoznanie's audited QA learning loop."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from osoznanie import (
    AccessEffect,
    AccessPolicy,
    AccessResourceKind,
    AuditedDecisionOrchestrator,
    AuditedDecisionRequest,
    AuditedDecisionStatus,
    AuthorizationEngine,
    AuthorizationQuery,
    Decision,
    DecisionProposal,
    Event,
    Evidence,
    Hypothesis,
    Lesson,
    MemoryObject,
    MemoryType,
    Outcome,
    OutcomeStatus,
    Reflection,
    SQLiteAccessPolicyStore,
    SQLiteAuthorizedMemoryStore,
    SQLiteDecisionTraceStore,
    SQLiteExperienceStore,
    TrustLevel,
    ValidationStatus,
)

DEMO_TIME = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
LESSON_KEY = "qa.lesson.checkout.browser-matrix"
REVIEW_ACTION = "release.review"
REQUIRED_CHECKS = (
    "desktop Chrome checkout",
    "Android Chrome checkout",
    "supported browser-device regression matrix",
)


@dataclass(frozen=True)
class QADemoResult:
    """Inspectable proof returned by the executable QA demonstrator."""

    initial_outcome_status: OutcomeStatus
    final_outcome_status: OutcomeStatus
    pipeline_status: AuditedDecisionStatus
    lesson_id: str
    lesson_memory_id: str
    policy_memory_id: str
    initial_trace_id: str
    outcome_trace_id: str
    final_outcome_id: str
    recalled_lesson: str
    checks_executed: tuple[str, ...]
    trace_memory_ids: tuple[str, ...]
    trace_policy_memory_ids: tuple[str, ...]
    trace_versions: tuple[int, int]


def _save_initial_failure(store: SQLiteExperienceStore) -> tuple[Outcome, Lesson, Event]:
    evidence = store.save(
        Evidence(
            id="evd_checkout_incident",
            source_type="incident-report",
            uri="demo://checkout/android-chrome/incident",
            trust_level=TrustLevel.VERIFIED,
            access_policy=AccessPolicy.OWNER_AND_AGENT,
            owner_id="human_alexey",
            agent_id="agent_qa",
            captured_at=DEMO_TIME,
            created_at=DEMO_TIME,
        )
    )
    event = store.save(
        Event(
            id="evt_checkout_release_1",
            actor_ids=["agent_qa", "human_alexey"],
            summary="Checkout failed for customers on Android Chrome after release.",
            context={"release": "1", "component": "checkout"},
            evidence_ids=[evidence.id],
            timestamp=DEMO_TIME,
            created_at=DEMO_TIME,
        )
    )
    decision = store.save(
        Decision(
            id="dec_checkout_release_1",
            event_id=event.id,
            agent_id="agent_qa",
            chosen_action="Approve after desktop Chrome smoke test.",
            alternatives_considered=["Test the supported browser-device matrix."],
            reasoning_summary="Desktop Chrome passed, so the release was approved.",
            evidence_ids=[evidence.id],
            confidence=0.63,
            created_at=DEMO_TIME + timedelta(minutes=1),
        )
    )
    outcome = store.save(
        Outcome(
            id="out_checkout_release_1",
            decision_id=decision.id,
            status=OutcomeStatus.FAILURE,
            summary="The release blocked checkout on a supported mobile configuration.",
            impact={"escaped_defect": True, "affected_platform": "Android Chrome"},
            evidence_ids=[evidence.id],
            observed_at=DEMO_TIME + timedelta(minutes=10),
            created_at=DEMO_TIME + timedelta(minutes=10),
        )
    )
    reflection = store.save(
        Reflection(
            id="ref_checkout_browser_matrix",
            source_ids=[event.id, decision.id, outcome.id],
            hypotheses=[
                Hypothesis(
                    statement="The release scope omitted Android Chrome coverage.",
                    confidence=0.92,
                    evidence_ids=[evidence.id],
                )
            ],
            limitations=["The demo uses a deterministic simulated release gate."],
            validation_status=ValidationStatus.HUMAN_APPROVED,
            created_at=DEMO_TIME + timedelta(minutes=15),
        )
    )
    lesson = store.save(
        Lesson(
            id="les_checkout_browser_matrix",
            statement=(
                "Test the supported browser-device matrix before approving "
                "customer-critical checkout changes."
            ),
            scope={
                "domain": "quality-assurance",
                "task_types": ["checkout-release-validation"],
                "tags": ["checkout", "chrome", "release"],
            },
            source_reflection_ids=[reflection.id],
            confidence=0.88,
            validation_status=ValidationStatus.HUMAN_APPROVED,
            effective_from=DEMO_TIME + timedelta(minutes=15),
            created_at=DEMO_TIME + timedelta(minutes=15),
        )
    )
    return outcome, lesson, event


def _save_lesson_memory(
    store: SQLiteExperienceStore,
    lesson: Lesson,
    source_event: Event,
) -> MemoryObject:
    return store.save(
        MemoryObject(
            id="mem_qa_checkout_browser_matrix_v1",
            memory_key=LESSON_KEY,
            memory_type=MemoryType.BEHAVIORAL_RULE,
            content={
                "lesson_id": lesson.id,
                "statement": lesson.statement,
                "required_checks": list(REQUIRED_CHECKS),
            },
            source_event_ids=[source_event.id],
            confidence=lesson.confidence,
            importance=1.0,
            valid_from=DEMO_TIME + timedelta(minutes=20),
            created_at=DEMO_TIME + timedelta(minutes=20),
            updated_at=DEMO_TIME + timedelta(minutes=20),
        )
    )


def _save_access_policy(store: SQLiteExperienceStore) -> MemoryObject:
    policy_event = store.save(
        Event(
            id="evt_policy_qa_release_review",
            actor_ids=["human_alexey"],
            summary="Allow the QA agent to use the validated checkout lesson.",
            timestamp=DEMO_TIME + timedelta(minutes=25),
            created_at=DEMO_TIME + timedelta(minutes=25),
        )
    )
    return store.save(
        MemoryObject(
            id="mem_policy_qa_release_review_v1",
            memory_key="access.agent_qa.qa.lesson.checkout.browser-matrix",
            memory_type=MemoryType.ACCESS_POLICY,
            content={
                "subject_id": "agent_qa",
                "action": REVIEW_ACTION,
                "resource": {
                    "kind": AccessResourceKind.EXACT_KEY.value,
                    "value": LESSON_KEY,
                },
                "effect": AccessEffect.ALLOW.value,
            },
            source_event_ids=[policy_event.id],
            confidence=1.0,
            importance=1.0,
            valid_from=DEMO_TIME + timedelta(minutes=25),
            created_at=DEMO_TIME + timedelta(minutes=25),
            updated_at=DEMO_TIME + timedelta(minutes=25),
        )
    )


def _save_second_release_decision(
    store: SQLiteExperienceStore,
) -> tuple[Evidence, Decision]:
    evidence = store.save(
        Evidence(
            id="evd_checkout_release_2",
            source_type="release-candidate",
            uri="demo://checkout/release-2",
            trust_level=TrustLevel.VERIFIED,
            access_policy=AccessPolicy.OWNER_AND_AGENT,
            owner_id="human_alexey",
            agent_id="agent_qa",
            captured_at=DEMO_TIME + timedelta(minutes=50),
            created_at=DEMO_TIME + timedelta(minutes=50),
        )
    )
    event = store.save(
        Event(
            id="evt_checkout_release_2",
            actor_ids=["agent_qa", "human_alexey"],
            summary="A new checkout release candidate requires validation.",
            context={"release": "2", "component": "checkout"},
            evidence_ids=[evidence.id],
            timestamp=DEMO_TIME + timedelta(minutes=50),
            created_at=DEMO_TIME + timedelta(minutes=50),
        )
    )
    decision = store.save(
        Decision(
            id="dec_checkout_release_2",
            event_id=event.id,
            agent_id="agent_qa",
            chosen_action="Run the authorized checkout release gate.",
            alternatives_considered=["Repeat desktop-only smoke testing."],
            reasoning_summary="A prior validated lesson requires browser-device coverage.",
            evidence_ids=[evidence.id],
            confidence=0.95,
            created_at=DEMO_TIME + timedelta(minutes=55),
        )
    )
    return evidence, decision


def run_demo(database: str | Path = ":memory:") -> QADemoResult:
    """Run the full audited QA learning loop and return inspectable proof."""

    with SQLiteExperienceStore(database) as store:
        failed_outcome, lesson, source_event = _save_initial_failure(store)
        lesson_memory = _save_lesson_memory(store, lesson, source_event)
        policy_memory = _save_access_policy(store)
        release_evidence, release_decision = _save_second_release_decision(store)

        trace_store = SQLiteDecisionTraceStore(store)
        orchestrator = AuditedDecisionOrchestrator(
            authorization=AuthorizationEngine(SQLiteAccessPolicyStore(store)),
            memory_store=SQLiteAuthorizedMemoryStore(store),
            trace_store=trace_store,
            outcome_store=store,
        )

        as_of = DEMO_TIME + timedelta(hours=1)
        decision_at = as_of + timedelta(minutes=5)
        request = AuditedDecisionRequest(
            authorization_query=AuthorizationQuery(
                requester_id="agent_qa",
                action=REVIEW_ACTION,
                as_of=as_of,
                memory_keys=[LESSON_KEY],
            ),
            agent_id="agent_qa",
            decision_at=decision_at,
        )

        applied_checks: list[str] = []
        recalled_lesson = ""

        def decide(context) -> DecisionProposal:
            nonlocal recalled_lesson
            if len(context.memory_view.entries) != 1:
                raise RuntimeError("the QA demo requires exactly one authorized lesson")
            memory = context.memory_view.entries[0].memory
            recalled_lesson = str(memory.content["statement"])
            applied_checks.extend(str(item) for item in memory.content["required_checks"])
            return DecisionProposal(
                action=context.authorized_action,
                alternatives_considered=["desktop-only smoke test"],
                reason_codes=["validated_prior_miss", "browser_matrix_required"],
                tool_name="qa.release-gate",
                tool_call_id="checkout-release-2",
                input_hash="sha256:checkout-release-2-demo",
            )

        def execute(_, trace) -> Outcome:
            if not trace_store.exists(trace.id):
                raise RuntimeError("trace must be durable before the release gate runs")
            return Outcome(
                id="out_checkout_release_2",
                decision_id=release_decision.id,
                status=OutcomeStatus.SUCCESS,
                summary=(
                    "The browser-device matrix detected the Android Chrome checkout "
                    "defect before release."
                ),
                impact={
                    "escaped_defect": False,
                    "prevented_release": True,
                    "checks_executed": list(applied_checks),
                },
                evidence_ids=[release_evidence.id],
                observed_at=decision_at + timedelta(minutes=5),
                created_at=decision_at + timedelta(minutes=5),
            )

        result = orchestrator.run(request, decide, execute)
        if (
            result.initial_trace_id is None
            or result.outcome_trace_id is None
            or result.outcome_id is None
        ):
            raise RuntimeError("the audited QA demo did not produce a complete trace chain")

        initial_trace = trace_store.get(result.initial_trace_id)
        outcome_trace = trace_store.get(result.outcome_trace_id)
        final_outcome = store.get(result.outcome_id)
        if not isinstance(final_outcome, Outcome):
            raise RuntimeError("the audited QA demo stored an invalid outcome record")

        return QADemoResult(
            initial_outcome_status=failed_outcome.status,
            final_outcome_status=final_outcome.status,
            pipeline_status=result.status,
            lesson_id=lesson.id,
            lesson_memory_id=lesson_memory.id,
            policy_memory_id=policy_memory.id,
            initial_trace_id=initial_trace.id,
            outcome_trace_id=outcome_trace.id,
            final_outcome_id=final_outcome.id,
            recalled_lesson=recalled_lesson,
            checks_executed=tuple(applied_checks),
            trace_memory_ids=tuple(initial_trace.memory_ids),
            trace_policy_memory_ids=tuple(initial_trace.policy_memory_ids),
            trace_versions=(initial_trace.trace_version, outcome_trace.trace_version),
        )


def main() -> None:
    result = run_demo()
    print("Osoznanie AI — audited QA learning loop")
    print(f"1. Previous release outcome: {result.initial_outcome_status.value}")
    print(f"2. Validated lesson: {result.recalled_lesson}")
    print("3. Checks applied to the next release:")
    for check in result.checks_executed:
        print(f"   - {check}")
    print(f"4. New release outcome: {result.final_outcome_status.value}")
    print(
        "5. Audit chain: "
        f"trace v{result.trace_versions[0]} {result.initial_trace_id} -> "
        f"trace v{result.trace_versions[1]} {result.outcome_trace_id}"
    )
    print(f"6. Pipeline status: {result.pipeline_status.value}")


if __name__ == "__main__":
    main()
