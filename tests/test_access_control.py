from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from osoznanie.access_control import (
    AccessDecisionTrace,
    AccessEffect,
    AccessReasonCode,
    AccessResourceKind,
    AuthorizationDecision,
    AuthorizationEngine,
    AuthorizationQuery,
    AuthorizedMemoryViewEngine,
)
from osoznanie.memory import MemoryObject, MemoryStatus, MemoryType
from osoznanie.memory_view import CommittedMemoryVersion
from osoznanie.models import Event
from osoznanie.sqlite_access_control import (
    SQLiteAccessPolicyStore,
    SQLiteAuthorizedMemoryStore,
)
from osoznanie.storage import SQLiteExperienceStore

T0 = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)


class FakePolicyStore:
    def __init__(self, history: list[CommittedMemoryVersion]) -> None:
        self.history = history

    def list_committed_memory_versions(self) -> list[CommittedMemoryVersion]:
        return list(self.history)


class FakeAuthorizedStore:
    def __init__(self, history: list[CommittedMemoryVersion]) -> None:
        self.history = history
        self.loaded_ids: list[str] = []

    def list_authorized_memory_versions(self, scope):
        allowed = [
            item
            for item in self.history
            if scope.allows(item.memory.memory_key, item.memory.memory_type)
        ]
        self.loaded_ids = [item.memory.id for item in allowed]
        return allowed


def policy(
    *,
    policy_key: str,
    resource_kind: AccessResourceKind,
    resource_value: str,
    effect: AccessEffect,
    subject_id: str = "agent_reader",
    action: str = "memory.read",
    version: int = 1,
    previous_id: str | None = None,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    valid_from: datetime = T0,
    committed_at: datetime = T0,
    memory_id: str | None = None,
) -> CommittedMemoryVersion:
    identifier = memory_id or f"mem_{policy_key}_{version}_{effect.value}"
    return CommittedMemoryVersion(
        memory=MemoryObject(
            id=identifier,
            memory_key=policy_key,
            memory_type=MemoryType.ACCESS_POLICY,
            content={
                "subject_id": subject_id,
                "action": action,
                "resource": {
                    "kind": resource_kind.value,
                    "value": resource_value,
                },
                "effect": effect.value,
            },
            source_event_ids=[f"evt_{identifier}"],
            confidence=1.0,
            importance=1.0,
            valid_from=valid_from,
            status=status,
            supersedes=[] if version == 1 else [previous_id or "mem_previous"],
            created_at=valid_from,
            updated_at=valid_from,
            version=version,
        ),
        committed_at=committed_at,
    )


def resource(
    key: str,
    *,
    memory_type: MemoryType = MemoryType.FACT,
    state: str = "visible",
) -> CommittedMemoryVersion:
    return CommittedMemoryVersion(
        memory=MemoryObject(
            id=f"mem_{key.replace('.', '_')}",
            memory_key=key,
            memory_type=memory_type,
            content={"state": state},
            source_event_ids=[f"evt_{key}"],
            confidence=0.9,
            importance=0.8,
            valid_from=T0,
            created_at=T0,
            updated_at=T0,
        ),
        committed_at=T0,
    )


def engine(
    policies: list[CommittedMemoryVersion],
    resources: list[CommittedMemoryVersion],
):
    authorized_store = FakeAuthorizedStore(resources)
    view_engine = AuthorizedMemoryViewEngine(
        AuthorizationEngine(FakePolicyStore(policies)),
        authorized_store,
    )
    return view_engine, authorized_store


def query(**overrides) -> AuthorizationQuery:
    values = {
        "requester_id": "agent_reader",
        "action": "memory.read",
        "as_of": T0 + timedelta(days=1),
        "memory_keys": ["profile.private"],
    }
    values.update(overrides)
    return AuthorizationQuery(**values)


def test_no_policy_is_deny_by_default() -> None:
    view_engine, store = engine([], [resource("profile.private")])

    view = view_engine.project(query())
    trace = view_engine.audit(query())

    assert view.entries == []
    assert view.rejections == []
    assert view.filter_counts.model_dump() == {
        "filtered_by_key_or_type": 0,
        "not_known_by_cutoff": 0,
        "not_yet_effective": 0,
        "superseded_versions": 0,
        "non_active_governing": 0,
        "expired_governing": 0,
    }
    assert store.loaded_ids == []
    assert trace.decision is AuthorizationDecision.DENY
    assert trace.reason_codes == [AccessReasonCode.DEFAULT_DENY]


def test_exact_allow_returns_only_authorized_memory() -> None:
    allow = policy(
        policy_key="access.agent_reader.profile.private",
        resource_kind=AccessResourceKind.EXACT_KEY,
        resource_value="profile.private",
        effect=AccessEffect.ALLOW,
    )
    view_engine, store = engine(
        [allow],
        [resource("profile.private"), resource("profile.other")],
    )

    view = view_engine.project(query())

    assert [entry.memory.memory_key for entry in view.entries] == ["profile.private"]
    assert store.loaded_ids == ["mem_profile_private"]


def test_denied_and_absent_keys_have_identical_external_shape() -> None:
    deny = policy(
        policy_key="access.agent_reader.profile.private",
        resource_kind=AccessResourceKind.EXACT_KEY,
        resource_value="profile.private",
        effect=AccessEffect.DENY,
    )
    allow_absent = policy(
        policy_key="access.agent_reader.profile.absent",
        resource_kind=AccessResourceKind.EXACT_KEY,
        resource_value="profile.absent",
        effect=AccessEffect.ALLOW,
    )
    denied_engine, _ = engine([deny], [resource("profile.private")])
    absent_engine, _ = engine([allow_absent], [resource("profile.private")])

    denied = denied_engine.project(query())
    absent = absent_engine.project(query(memory_keys=["profile.absent"]))

    assert denied.model_dump() == absent.model_dump()
    assert "access" not in denied.model_dump_json()
    assert "profile.private" not in denied.model_dump_json()


def test_internal_audit_records_explicit_deny_and_policy_provenance() -> None:
    deny = policy(
        policy_key="access.agent_reader.profile.private",
        resource_kind=AccessResourceKind.EXACT_KEY,
        resource_value="profile.private",
        effect=AccessEffect.DENY,
    )
    view_engine, _ = engine([deny], [resource("profile.private")])

    trace: AccessDecisionTrace = view_engine.audit(query())

    assert trace.decision is AuthorizationDecision.DENY
    assert trace.reason_codes == [AccessReasonCode.EXPLICIT_DENY]
    assert trace.matched_policy_memory_ids == [deny.memory.id]
    assert trace.requested_memory_keys == ["profile.private"]


def test_specific_deny_overrides_broader_prefix_allow() -> None:
    broad_allow = policy(
        policy_key="access.agent_reader.profile",
        resource_kind=AccessResourceKind.KEY_PREFIX,
        resource_value="profile.",
        effect=AccessEffect.ALLOW,
    )
    exact_deny = policy(
        policy_key="access.agent_reader.profile.secret",
        resource_kind=AccessResourceKind.EXACT_KEY,
        resource_value="profile.secret",
        effect=AccessEffect.DENY,
    )
    view_engine, store = engine(
        [broad_allow, exact_deny],
        [resource("profile.public"), resource("profile.secret")],
    )

    view = view_engine.project(
        query(memory_keys=[], key_prefixes=["profile."])
    )

    assert [entry.memory.memory_key for entry in view.entries] == ["profile.public"]
    assert store.loaded_ids == ["mem_profile_public"]


def test_revoked_governing_allow_never_resurrects_old_grant() -> None:
    grant = policy(
        policy_key="access.agent_reader.profile.private",
        resource_kind=AccessResourceKind.EXACT_KEY,
        resource_value="profile.private",
        effect=AccessEffect.ALLOW,
    )
    revoke = policy(
        policy_key=grant.memory.memory_key,
        resource_kind=AccessResourceKind.EXACT_KEY,
        resource_value="profile.private",
        effect=AccessEffect.ALLOW,
        version=2,
        previous_id=grant.memory.id,
        status=MemoryStatus.REVOKED,
        valid_from=T0 + timedelta(hours=1),
        committed_at=T0 + timedelta(hours=1),
    )
    view_engine, _ = engine([grant, revoke], [resource("profile.private")])

    view = view_engine.project(query())
    trace = view_engine.audit(query())

    assert view.entries == []
    assert trace.decision is AuthorizationDecision.DENY
    assert trace.reason_codes == [AccessReasonCode.DEFAULT_DENY]


def test_known_at_prevents_late_backdated_policy_from_rewriting_access_history() -> None:
    late_grant = policy(
        policy_key="access.agent_reader.profile.private",
        resource_kind=AccessResourceKind.EXACT_KEY,
        resource_value="profile.private",
        effect=AccessEffect.ALLOW,
        valid_from=T0,
        committed_at=T0 + timedelta(days=20),
    )
    view_engine, _ = engine([late_grant], [resource("profile.private")])

    historical = view_engine.project(
        query(known_at=T0 + timedelta(days=10))
    )
    retrospective = view_engine.project(query(known_at=None))

    assert historical.entries == []
    assert [entry.memory.memory_key for entry in retrospective.entries] == [
        "profile.private"
    ]


def test_ambiguous_policy_history_fails_closed() -> None:
    first = policy(
        policy_key="access.agent_reader.profile.private",
        resource_kind=AccessResourceKind.EXACT_KEY,
        resource_value="profile.private",
        effect=AccessEffect.ALLOW,
        memory_id="mem_policy_a",
    )
    duplicate = policy(
        policy_key=first.memory.memory_key,
        resource_kind=AccessResourceKind.EXACT_KEY,
        resource_value="profile.private",
        effect=AccessEffect.ALLOW,
        memory_id="mem_policy_b",
    )
    view_engine, _ = engine([first, duplicate], [resource("profile.private")])

    view = view_engine.project(query())
    trace = view_engine.audit(query())

    assert view.entries == []
    assert trace.decision is AuthorizationDecision.DENY
    assert trace.reason_codes == [AccessReasonCode.POLICY_HISTORY_AMBIGUOUS]


def test_naive_authorization_timestamps_are_rejected() -> None:
    with pytest.raises(ValidationError, match="as_of must be timezone-aware"):
        query(as_of=datetime(2026, 6, 2, 9, 0))
    with pytest.raises(ValidationError, match="known_at must be timezone-aware"):
        query(known_at=datetime(2026, 6, 2, 9, 0))


def save_event(store: SQLiteExperienceStore, event_id: str) -> None:
    store.save(
        Event(
            id=event_id,
            actor_ids=["system"],
            summary=event_id,
            timestamp=T0,
            created_at=T0,
        )
    )


def save_memory(store: SQLiteExperienceStore, item: CommittedMemoryVersion) -> None:
    for event_id in item.memory.source_event_ids:
        save_event(store, event_id)
    store.save(item.memory)


def test_sqlite_adapter_never_deserializes_denied_payloads() -> None:
    store = SQLiteExperienceStore()
    broad_allow = policy(
        policy_key="access.agent_reader.records",
        resource_kind=AccessResourceKind.KEY_PREFIX,
        resource_value="records.",
        effect=AccessEffect.ALLOW,
    )
    exact_deny = policy(
        policy_key="access.agent_reader.records.secret",
        resource_kind=AccessResourceKind.EXACT_KEY,
        resource_value="records.secret",
        effect=AccessEffect.DENY,
    )
    public = resource("records.public")
    secret = resource("records.secret")
    for item in [broad_allow, exact_deny, public, secret]:
        save_memory(store, item)

    policy_store = SQLiteAccessPolicyStore(store)
    authorized_store = SQLiteAuthorizedMemoryStore(store)
    view_engine = AuthorizedMemoryViewEngine(
        AuthorizationEngine(policy_store),
        authorized_store,
    )

    view = view_engine.project(
        query(memory_keys=[], key_prefixes=["records."])
    )

    assert [entry.memory.id for entry in view.entries] == [public.memory.id]
    assert authorized_store.last_loaded_memory_ids == [public.memory.id]
    assert secret.memory.id not in authorized_store.last_loaded_memory_ids
