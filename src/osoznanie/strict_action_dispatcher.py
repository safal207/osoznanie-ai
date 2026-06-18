"""Fail-closed runtime guard for typed action adapters."""

from __future__ import annotations

from .action_attempt import ActionAttempt
from .action_dispatcher import (
    ActionWorkerDispatcher,
    ToolExecutionResult,
)
from .action_outbox import ActionIntent


class StrictActionWorkerDispatcher(ActionWorkerDispatcher):
    """Reject malformed adapter returns before terminal finalization."""

    def _execute(
        self,
        intent: ActionIntent,
        started: ActionAttempt,
    ) -> ToolExecutionResult:
        result = super()._execute(intent, started)
        if isinstance(result, ToolExecutionResult):
            return result
        return ToolExecutionResult.permanent("invalid_adapter_result")
