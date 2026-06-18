from datetime import UTC, datetime, timedelta

from osoznanie.decision_trace import DecisionTrace, TraceAuthorizationDecision
from osoznanie.decision_trace_store import SQLiteDecisionTraceStore
from osoznanie.memory import MemoryObject, MemoryType
from osoznanie.models import Decision, Event, Outcome, OutcomeStatus
from osoznanie.orchestration import DecisionProposal
from osoznanie.sqlite_action_outbox import SQLiteActionOutbox
from osoznanie.storage import SQLiteExperienceStore

T0 = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)


def make_proposal(
    *,
    action: str = "report.generate",
    tool_name: str = "report.tool",
    tool_call_id: str = "call_1",
    input_hash: str = "sha256:input",
) -> DecisionProposal:
    return DecisionProposal(
        action=action,
        alternatives_considered=["defer"],
        reason_codes=["context_verified"],
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        input_hash=input_hash,
    )


def save_memory_reference(
    store: SQLiteExperienceStore,
    memory_id: str,
    memory_type: MemoryType,
) -> None:
    event_id = f"evt_{memory_id}"
    store.save(
        Event(
            id=event_id,
            actor_ids=["system"],
            summary=f"Source for {memory_id}",
            timestamp=T0,
            created_at=T0,
        )
    )
    store.save(
        MemoryObject(
            id=memory_id,
            memory_key=f"test.{memory_id}",
            memory_type=memory_type,
            content={"value": memory_id},
            source_event_ids=[event_id],
            confidence=1.0,
            importance=1.0,
            valid_from=T0,
            created_at=T0,
            updated_at=T0,
        )
    )


def prepare_trace(
    store: SQLiteExperienceStore,
    suffix: str = "one",
    *,
    created_at: datetime = T0,
    decision_at: datetime | None = None,
    action: str = "report.generate",
    tool_name: str = "report.tool",
    tool_call_id: str = "call_1",
    input_hash: str = "sha256:input",
) -> DecisionTrace:
    policy_id = f"mem_policy_{suffix}"
    memory_id = f"mem_state_{suffix}"
    save_memory_reference(store, policy_id, MemoryType.ACCESS_POLICY)
    save_memory_reference(store, memory_id, MemoryType.FACT)
    return DecisionTrace(
        id=f"dtr_{suffix}",
        requester_id="agent_reader",
        agent_id="agent_executor",
        action=action,
        authorization_decision=TraceAuthorizationDecision.ALLOW,
        policy_memory_ids=[policy_id],
        memory_ids=[memory_id],
        as_of=created_at,
        known_at=created_at,
        decision_at=decision_at or created_at + timedelta(minutes=1),
        alternatives_considered=["defer"],
        reason_codes=["context_verified"],
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        input_hash=input_hash,
        created_at=created_at,
    )


def make_outbox():
    store = SQLiteExperienceStore()
    trace_store = SQLiteDecisionTraceStore(store)
    outbox = SQLiteActionOutbox(store, trace_store)
    return store, trace_store, outbox


def save_outcome(
    store: SQLiteExperienceStore,
    suffix: str = "one",
    *,
    observed_at: datetime | None = None,
) -> Outcome:
    event = Event(
        id=f"evt_action_{suffix}",
        actor_ids=["agent_executor"],
        summary="Action requested.",
        timestamp=T0,
        created_at=T0,
    )
    store.save(event)
    decision = Decision(
        id=f"dec_action_{suffix}",
        event_id=event.id,
        agent_id="agent_executor",
        chosen_action="report.generate",
        alternatives_considered=["defer"],
        reasoning_summary="Audited action.",
        evidence_ids=[],
        confidence=1.0,
        created_at=T0 + timedelta(minutes=1),
    )
    store.save(decision)
    outcome_time = observed_at or T0 + timedelta(minutes=2)
    outcome = Outcome(
        id=f"out_action_{suffix}",
        decision_id=decision.id,
        status=OutcomeStatus.SUCCESS,
        summary="Action completed.",
        observed_at=outcome_time,
        created_at=outcome_time,
    )
    store.save(outcome)
    return outcome
