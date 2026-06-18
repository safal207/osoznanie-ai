"""Durable worker dispatch with typed tool adapters and protected input resolution."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from .action_attempt import ActionAttempt
from .action_attempt_store import SQLiteActionAttemptStore
from .action_finalizer import SQLiteActionFinalizer
from .action_outbox import ActionIntent, aware_utc
from .sqlite_action_outbox import SQLiteActionOutbox
from .storage import SQLiteExperienceStore


class ActionDispatcherError(RuntimeError):
    """Base exception for worker-dispatch configuration and contracts."""


class DuplicateToolAdapterError(ActionDispatcherError):
    """Two adapters registered the same normalized tool name."""


class RetryableToolError(ActionDispatcherError):
    """A resolver or adapter failed in a way that may succeed on retry."""

    def __init__(self, error_code: str, retry_after: timedelta | None = None) -> None:
        super().__init__(error_code)
        self.error_code = _required_text(error_code, field_name="error_code")
        self.retry_after = retry_after
        if retry_after is not None and retry_after.total_seconds() <= 0:
            raise ValueError("retry_after must be positive")


class PermanentToolError(ActionDispatcherError):
    """A resolver or adapter failed in a way that must not be retried."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = _required_text(error_code, field_name="error_code")


class ToolExecutionKind(StrEnum):
    SUCCEEDED = "succeeded"
    RETRYABLE_FAILURE = "retryable_failure"
    PERMANENT_FAILURE = "permanent_failure"


class DispatcherStatus(StrEnum):
    NO_WORK = "no_work"
    COMPLETED = "completed"
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED = "failed"


def _required_text(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


class ResolvedToolInput(BaseModel):
    """Ephemeral protected input; the dispatcher never persists payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    payload: Mapping[str, object]
    input_hash: str | None = None

    @field_validator("input_hash")
    @classmethod
    def normalize_input_hash(cls, value: str | None) -> str | None:
        return _optional_text(value)


class ToolExecutionResult(BaseModel):
    """Safe terminal classification returned by a typed adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ToolExecutionKind
    outcome_id: str | None = None
    error_code: str | None = None
    response_hash: str | None = None
    retry_after: timedelta | None = None

    @field_validator("outcome_id", "error_code", "response_hash")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def validate_result(self) -> ToolExecutionResult:
        if self.kind is ToolExecutionKind.SUCCEEDED:
            if self.outcome_id is None:
                raise ValueError("successful tool execution requires outcome_id")
            if self.error_code is not None or self.retry_after is not None:
                raise ValueError("successful tool execution cannot contain failure fields")
            return self

        if self.error_code is None:
            raise ValueError("failed tool execution requires error_code")
        if self.outcome_id is not None:
            raise ValueError("failed tool execution cannot contain outcome_id")
        if self.kind is ToolExecutionKind.RETRYABLE_FAILURE:
            if self.retry_after is not None and self.retry_after.total_seconds() <= 0:
                raise ValueError("retry_after must be positive")
        elif self.retry_after is not None:
            raise ValueError("permanent failure cannot contain retry_after")
        return self

    @classmethod
    def succeeded(
        cls,
        outcome_id: str,
        *,
        response_hash: str | None = None,
    ) -> ToolExecutionResult:
        return cls(
            kind=ToolExecutionKind.SUCCEEDED,
            outcome_id=outcome_id,
            response_hash=response_hash,
        )

    @classmethod
    def retryable(
        cls,
        error_code: str,
        *,
        retry_after: timedelta | None = None,
        response_hash: str | None = None,
    ) -> ToolExecutionResult:
        return cls(
            kind=ToolExecutionKind.RETRYABLE_FAILURE,
            error_code=error_code,
            retry_after=retry_after,
            response_hash=response_hash,
        )

    @classmethod
    def permanent(
        cls,
        error_code: str,
        *,
        response_hash: str | None = None,
    ) -> ToolExecutionResult:
        return cls(
            kind=ToolExecutionKind.PERMANENT_FAILURE,
            error_code=error_code,
            response_hash=response_hash,
        )


@dataclass(frozen=True)
class ToolExecutionContext:
    intent_id: str
    trace_id: str
    worker_id: str
    tool_call_id: str | None
    idempotency_key: str
    started_attempt_id: str


@dataclass(frozen=True)
class DispatcherResult:
    status: DispatcherStatus
    intent_id: str | None = None
    started_attempt_id: str | None = None
    terminal_attempt_id: str | None = None
    outcome_id: str | None = None
    error_code: str | None = None
    retry_at: datetime | None = None


class SecureToolInputResolver(Protocol):
    """Resolve protected input from a trusted external store."""

    def resolve(self, intent: ActionIntent) -> ResolvedToolInput: ...


class ToolAdapter(Protocol):
    """Runtime-typed adapter contract for one external tool."""

    tool_name: str
    input_model: type[BaseModel]

    def execute(
        self,
        request: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult: ...


class ToolAdapterRegistry:
    """Immutable normalized lookup for heterogeneous typed adapters."""

    def __init__(self, adapters: Sequence[ToolAdapter]) -> None:
        registry: dict[str, ToolAdapter] = {}
        for adapter in adapters:
            name = _required_text(adapter.tool_name, field_name="tool_name")
            if name in registry:
                raise DuplicateToolAdapterError(f"duplicate tool adapter: {name}")
            registry[name] = adapter
        self._adapters = registry

    def get(self, tool_name: str) -> ToolAdapter | None:
        return self._adapters.get(tool_name.strip())


class ActionWorkerDispatcher:
    """Claim, evidence, execute, and atomically finalize one durable action."""

    def __init__(
        self,
        store: SQLiteExperienceStore,
        worker_id: str,
        input_resolver: SecureToolInputResolver,
        adapters: Sequence[ToolAdapter],
        *,
        lease_for: timedelta = timedelta(minutes=5),
        default_retry_after: timedelta = timedelta(minutes=1),
        clock: Callable[[], datetime] | None = None,
        outbox: SQLiteActionOutbox | None = None,
        attempt_store: SQLiteActionAttemptStore | None = None,
        finalizer: SQLiteActionFinalizer | None = None,
    ) -> None:
        self.store = store
        self.worker_id = _required_text(worker_id, field_name="worker_id")
        if lease_for.total_seconds() <= 0:
            raise ValueError("lease_for must be positive")
        if default_retry_after.total_seconds() <= 0:
            raise ValueError("default_retry_after must be positive")
        self.lease_for = lease_for
        self.default_retry_after = default_retry_after
        self.clock = clock or (lambda: datetime.now(UTC))
        self.input_resolver = input_resolver
        self.registry = ToolAdapterRegistry(adapters)
        self.outbox = outbox or SQLiteActionOutbox(store)
        self.attempt_store = attempt_store or SQLiteActionAttemptStore(
            store,
            self.outbox,
        )
        self.finalizer = finalizer or SQLiteActionFinalizer(
            store,
            self.outbox,
            self.attempt_store,
        )

    def dispatch_once(self) -> DispatcherResult:
        claimed_at = self._now()
        intent = self.outbox.claim(self.worker_id, claimed_at, self.lease_for)
        if intent is None:
            return DispatcherResult(status=DispatcherStatus.NO_WORK)

        lease_token = intent.lease_token
        if lease_token is None:
            raise ActionDispatcherError("claimed action intent is missing lease_token")
        started = self.attempt_store.start(
            intent,
            self.worker_id,
            lease_token,
            claimed_at,
        )
        execution = self._execute(intent, started)
        finished_at = self._now()
        return self._finalize(
            intent,
            started,
            lease_token,
            execution,
            finished_at,
        )

    def _execute(
        self,
        intent: ActionIntent,
        started: ActionAttempt,
    ) -> ToolExecutionResult:
        adapter = self.registry.get(intent.tool_name)
        if adapter is None:
            return ToolExecutionResult.permanent("unknown_tool")

        try:
            resolved = self.input_resolver.resolve(intent)
        except RetryableToolError as error:
            return ToolExecutionResult.retryable(
                error.error_code,
                retry_after=error.retry_after,
            )
        except PermanentToolError as error:
            return ToolExecutionResult.permanent(error.error_code)
        except Exception:
            return ToolExecutionResult.retryable("input_resolver_exception")

        if resolved.input_hash != intent.input_hash:
            return ToolExecutionResult.permanent("input_hash_mismatch")

        try:
            request = adapter.input_model.model_validate(dict(resolved.payload))
        except ValidationError:
            return ToolExecutionResult.permanent("input_validation_failed")

        context = ToolExecutionContext(
            intent_id=intent.id,
            trace_id=intent.trace_id,
            worker_id=self.worker_id,
            tool_call_id=intent.tool_call_id,
            idempotency_key=intent.idempotency_key,
            started_attempt_id=started.id,
        )
        try:
            return adapter.execute(request, context)
        except RetryableToolError as error:
            return ToolExecutionResult.retryable(
                error.error_code,
                retry_after=error.retry_after,
            )
        except PermanentToolError as error:
            return ToolExecutionResult.permanent(error.error_code)
        except Exception:
            return ToolExecutionResult.retryable("adapter_exception")

    def _finalize(
        self,
        intent: ActionIntent,
        started: ActionAttempt,
        lease_token: str,
        execution: ToolExecutionResult,
        finished_at: datetime,
    ) -> DispatcherResult:
        if execution.kind is ToolExecutionKind.SUCCEEDED:
            finalized = self.finalizer.complete(
                started,
                lease_token,
                finished_at,
                execution.outcome_id or "",
                response_hash=execution.response_hash,
            )
            return DispatcherResult(
                status=DispatcherStatus.COMPLETED,
                intent_id=intent.id,
                started_attempt_id=started.id,
                terminal_attempt_id=finalized.attempt.id,
                outcome_id=execution.outcome_id,
            )

        if execution.kind is ToolExecutionKind.RETRYABLE_FAILURE:
            retry_at = finished_at + (
                execution.retry_after or self.default_retry_after
            )
            finalized = self.finalizer.fail(
                started,
                lease_token,
                finished_at,
                execution.error_code or "retryable_failure",
                retry_at=retry_at,
                response_hash=execution.response_hash,
            )
            return DispatcherResult(
                status=DispatcherStatus.RETRY_SCHEDULED,
                intent_id=intent.id,
                started_attempt_id=started.id,
                terminal_attempt_id=finalized.attempt.id,
                error_code=execution.error_code,
                retry_at=retry_at,
            )

        finalized = self.finalizer.fail(
            started,
            lease_token,
            finished_at,
            execution.error_code or "permanent_failure",
            response_hash=execution.response_hash,
        )
        return DispatcherResult(
            status=DispatcherStatus.FAILED,
            intent_id=intent.id,
            started_attempt_id=started.id,
            terminal_attempt_id=finalized.attempt.id,
            error_code=execution.error_code,
        )

    def _now(self) -> datetime:
        return aware_utc(self.clock(), field_name="clock")
