"""Bitemporal, deny-by-default authorization for memory projection."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .consolidation import AmbiguousMemoryHistoryError
from .memory import MemoryObject, MemoryType
from .memory_view import (
    CommittedMemoryVersion,
    MemoryView,
    MemoryViewEngine,
    MemoryViewFilterCounts,
    MemoryViewQuery,
    MemoryViewStore,
    _require_aware_utc,
)


class AccessEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class AccessResourceKind(StrEnum):
    EXACT_KEY = "exact_key"
    KEY_PREFIX = "key_prefix"
    MEMORY_TYPE = "memory_type"


class AuthorizationDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class AccessReasonCode(StrEnum):
    POLICY_ALLOWED = "policy_allowed"
    DEFAULT_DENY = "default_deny"
    EXPLICIT_DENY = "explicit_deny"
    POLICY_HISTORY_AMBIGUOUS = "policy_history_ambiguous"
    MALFORMED_POLICY = "malformed_policy"


class AccessResource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: AccessResourceKind
    value: str = Field(min_length=1)

    @field_validator("value")
    @classmethod
    def normalize_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("resource value must not be blank")
        return normalized


class AccessPolicyContent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    resource: AccessResource
    effect: AccessEffect

    @field_validator("subject_id", "action")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("policy fields must not be blank")
        return normalized


class AccessPolicy(BaseModel):
    """Typed active policy derived from an immutable policy MemoryObject."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: str
    memory_key: str
    content: AccessPolicyContent

    @classmethod
    def from_memory(cls, memory: MemoryObject) -> AccessPolicy:
        if memory.memory_type is not MemoryType.ACCESS_POLICY:
            raise ValueError("memory is not an access policy")
        return cls(
            memory_id=memory.id,
            memory_key=memory.memory_key,
            content=AccessPolicyContent.model_validate(memory.content),
        )


class AuthorizationQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requester_id: str = Field(min_length=1)
    action: str = Field(default="memory.read", min_length=1)
    as_of: datetime
    known_at: datetime | None = None
    memory_keys: list[str] = Field(default_factory=list)
    key_prefixes: list[str] = Field(default_factory=list)
    memory_types: list[MemoryType] = Field(default_factory=list)

    @field_validator("requester_id", "action")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("authorization fields must not be blank")
        return normalized

    @field_validator("as_of")
    @classmethod
    def normalize_as_of(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, field_name="as_of")

    @field_validator("known_at")
    @classmethod
    def normalize_known_at(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_aware_utc(value, field_name="known_at")

    @field_validator("memory_keys", "key_prefixes")
    @classmethod
    def normalize_selectors(cls, values: list[str]) -> list[str]:
        return sorted({value.strip() for value in values if value.strip()})

    @field_validator("memory_types")
    @classmethod
    def normalize_types(cls, values: list[MemoryType]) -> list[MemoryType]:
        return sorted(set(values), key=lambda item: item.value)


class AuthorizedRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_memory_id: str
    resource: AccessResource
    effect: AccessEffect

    def matches(self, memory_key: str, memory_type: MemoryType) -> bool:
        if self.resource.kind is AccessResourceKind.EXACT_KEY:
            return memory_key == self.resource.value
        if self.resource.kind is AccessResourceKind.KEY_PREFIX:
            return memory_key.startswith(self.resource.value)
        return memory_type.value == self.resource.value

    def specificity(self) -> tuple[int, int]:
        if self.resource.kind is AccessResourceKind.EXACT_KEY:
            return (3, len(self.resource.value))
        if self.resource.kind is AccessResourceKind.KEY_PREFIX:
            return (2, len(self.resource.value))
        return (1, 0)


class AuthorizedScope(BaseModel):
    """Internal scope consumed by a restricted read adapter, never returned externally."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requested_memory_keys: list[str]
    requested_key_prefixes: list[str]
    requested_memory_types: list[MemoryType]
    rules: list[AuthorizedRule]

    def requested(self, memory_key: str, memory_type: MemoryType) -> bool:
        if not (
            self.requested_memory_keys
            or self.requested_key_prefixes
            or self.requested_memory_types
        ):
            return True
        return (
            memory_key in self.requested_memory_keys
            or any(memory_key.startswith(prefix) for prefix in self.requested_key_prefixes)
            or memory_type in self.requested_memory_types
        )

    def winning_rule(
        self,
        memory_key: str,
        memory_type: MemoryType,
    ) -> AuthorizedRule | None:
        matching = [
            rule
            for rule in self.rules
            if rule.matches(memory_key, memory_type)
        ]
        if not matching:
            return None
        return max(
            matching,
            key=lambda rule: (
                *rule.specificity(),
                1 if rule.effect is AccessEffect.DENY else 0,
                rule.policy_memory_id,
            ),
        )

    def allows(self, memory_key: str, memory_type: MemoryType) -> bool:
        if memory_type is MemoryType.ACCESS_POLICY or not self.requested(
            memory_key,
            memory_type,
        ):
            return False
        winner = self.winning_rule(memory_key, memory_type)
        return winner is not None and winner.effect is AccessEffect.ALLOW

    def has_potential_allow(self) -> bool:
        return any(rule.effect is AccessEffect.ALLOW for rule in self.rules)


class AccessDecisionTrace(BaseModel):
    """Privileged audit result. It must never be embedded in external MemoryView."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requester_id: str
    action: str
    as_of: datetime
    known_at: datetime | None
    decision: AuthorizationDecision
    reason_codes: list[AccessReasonCode]
    matched_policy_memory_ids: list[str]
    requested_memory_keys: list[str]
    requested_key_prefixes: list[str]
    requested_memory_types: list[MemoryType]


class AuthorizationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: AuthorizedScope
    trace: AccessDecisionTrace


class AccessPolicyStore(MemoryViewStore, Protocol):
    """Root-capability read path for policy memories only."""


class AuthorizedMemoryStore(Protocol):
    """Read protected memory only after receiving an internal authorized scope."""

    def list_authorized_memory_versions(
        self,
        scope: AuthorizedScope,
    ) -> list[CommittedMemoryVersion]: ...


class AuthorizationEngine:
    def __init__(self, policy_store: AccessPolicyStore) -> None:
        self.policy_store = policy_store

    def authorize(self, query: AuthorizationQuery) -> AuthorizationResult:
        empty_scope = AuthorizedScope(
            requested_memory_keys=query.memory_keys,
            requested_key_prefixes=query.key_prefixes,
            requested_memory_types=query.memory_types,
            rules=[],
        )
        try:
            policy_view = MemoryViewEngine(self.policy_store).project(
                MemoryViewQuery(
                    as_of=query.as_of,
                    known_at=query.known_at,
                    memory_types=[MemoryType.ACCESS_POLICY],
                )
            )
        except AmbiguousMemoryHistoryError:
            return self._result(
                query,
                empty_scope,
                AuthorizationDecision.DENY,
                [AccessReasonCode.POLICY_HISTORY_AMBIGUOUS],
                [],
            )

        policies: list[AccessPolicy] = []
        try:
            for entry in policy_view.entries:
                policy = AccessPolicy.from_memory(entry.memory)
                if (
                    policy.content.subject_id == query.requester_id
                    and policy.content.action == query.action
                ):
                    policies.append(policy)
        except (ValidationError, ValueError):
            return self._result(
                query,
                empty_scope,
                AuthorizationDecision.DENY,
                [AccessReasonCode.MALFORMED_POLICY],
                [],
            )

        rules = [
            AuthorizedRule(
                policy_memory_id=policy.memory_id,
                resource=policy.content.resource,
                effect=policy.content.effect,
            )
            for policy in sorted(policies, key=lambda item: item.memory_id)
        ]
        scope = AuthorizedScope(
            requested_memory_keys=query.memory_keys,
            requested_key_prefixes=query.key_prefixes,
            requested_memory_types=query.memory_types,
            rules=rules,
        )
        matched_ids = [rule.policy_memory_id for rule in rules]
        if not scope.has_potential_allow():
            reason = (
                AccessReasonCode.EXPLICIT_DENY
                if any(rule.effect is AccessEffect.DENY for rule in rules)
                else AccessReasonCode.DEFAULT_DENY
            )
            return self._result(
                query,
                scope,
                AuthorizationDecision.DENY,
                [reason],
                matched_ids,
            )
        return self._result(
            query,
            scope,
            AuthorizationDecision.ALLOW,
            [AccessReasonCode.POLICY_ALLOWED],
            matched_ids,
        )

    @staticmethod
    def _result(
        query: AuthorizationQuery,
        scope: AuthorizedScope,
        decision: AuthorizationDecision,
        reasons: list[AccessReasonCode],
        matched_ids: list[str],
    ) -> AuthorizationResult:
        return AuthorizationResult(
            scope=scope,
            trace=AccessDecisionTrace(
                requester_id=query.requester_id,
                action=query.action,
                as_of=query.as_of,
                known_at=query.known_at,
                decision=decision,
                reason_codes=reasons,
                matched_policy_memory_ids=matched_ids,
                requested_memory_keys=query.memory_keys,
                requested_key_prefixes=query.key_prefixes,
                requested_memory_types=query.memory_types,
            ),
        )


class _AuthorizedProjectionStore:
    def __init__(
        self,
        store: AuthorizedMemoryStore,
        scope: AuthorizedScope,
    ) -> None:
        self.store = store
        self.scope = scope

    def list_committed_memory_versions(self) -> list[CommittedMemoryVersion]:
        return self.store.list_authorized_memory_versions(self.scope)


class AuthorizedMemoryViewEngine:
    """External non-disclosing view plus a separate privileged audit method."""

    def __init__(
        self,
        authorization: AuthorizationEngine,
        memory_store: AuthorizedMemoryStore,
    ) -> None:
        self.authorization = authorization
        self.memory_store = memory_store

    def project(self, query: AuthorizationQuery) -> MemoryView:
        result = self.authorization.authorize(query)
        if not result.scope.has_potential_allow():
            return self._empty_view(query)

        view = MemoryViewEngine(
            _AuthorizedProjectionStore(self.memory_store, result.scope)
        ).project(
            MemoryViewQuery(
                as_of=query.as_of,
                known_at=query.known_at,
            )
        )
        # The restricted store never returns denied records. No access rejection,
        # hidden count, selector, or policy identifier enters this external result.
        return view

    def audit(self, query: AuthorizationQuery) -> AccessDecisionTrace:
        return self.authorization.authorize(query).trace

    @staticmethod
    def _empty_view(query: AuthorizationQuery) -> MemoryView:
        return MemoryView(
            as_of=query.as_of,
            known_at=query.known_at,
            entries=[],
            rejections=[],
            filter_counts=MemoryViewFilterCounts(),
        )
