"""Typed Osoznanie adapter for Playwright QA evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel

from .action_dispatcher import ToolExecutionContext, ToolExecutionResult
from .models import Decision, Event, Outcome, OutcomeStatus
from .playwright_runner import (
    BrowserCheckEvidence,
    PlaywrightBrowserCheckRunner,
    PlaywrightCheckInput,
)
from .storage import DuplicateRecordError, SQLiteExperienceStore


class BrowserCheckRunner(Protocol):
    def run(self, request: PlaywrightCheckInput) -> BrowserCheckEvidence: ...


class PlaywrightQAAdapter:
    """Persist safe browser-check evidence and return a typed tool result."""

    tool_name = "qa.playwright_check"
    input_model = PlaywrightCheckInput

    def __init__(
        self,
        store: SQLiteExperienceStore,
        observed_at: datetime,
        runner: BrowserCheckRunner | None = None,
    ) -> None:
        self.store = store
        self.observed_at = observed_at
        self.runner = runner or PlaywrightBrowserCheckRunner()

    def execute(
        self,
        request: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        if not isinstance(request, PlaywrightCheckInput):
            return ToolExecutionResult.permanent("invalid_request_model")

        suffix = hashlib.sha256(
            context.idempotency_key.encode("utf-8")
        ).hexdigest()[:16]
        outcome_id = f"out_pw_{suffix}"
        if self.store.exists(outcome_id):
            existing = self.store.get(outcome_id)
            if not isinstance(existing, Outcome):
                return ToolExecutionResult.permanent("outcome_record_conflict")
            return ToolExecutionResult.succeeded(
                existing.id,
                response_hash=_response_hash(existing),
            )

        evidence = self.runner.run(request)
        event = Event(
            id=f"evt_pw_{suffix}",
            actor_ids=[context.worker_id],
            summary=(
                f"Playwright checked release {request.release_id} in "
                f"{request.browser.value}."
            ),
            context={
                "release_id": request.release_id,
                "browser": request.browser.value,
                "changed_components": sorted(request.changed_components),
                "target": evidence.target,
                "check_code": evidence.code.value,
                "duration_ms": evidence.duration_ms,
            },
            timestamp=self.observed_at,
            created_at=self.observed_at,
        )
        decision = Decision(
            id=f"dec_pw_{suffix}",
            event_id=event.id,
            agent_id=context.worker_id,
            chosen_action="qa.browser_check",
            alternatives_considered=["approve_without_browser_check"],
            reasoning_summary=(
                "Executed the remembered browser regression rule before approval."
            ),
            confidence=1.0,
            created_at=self.observed_at,
        )
        release_gate = "clear" if evidence.passed else "blocked"
        outcome = Outcome(
            id=outcome_id,
            decision_id=decision.id,
            status=(
                OutcomeStatus.SUCCESS if evidence.passed else OutcomeStatus.FAILURE
            ),
            summary=(
                "Browser regression check passed."
                if evidence.passed
                else f"Browser regression detected: {evidence.code.value}."
            ),
            impact={
                "release_id": request.release_id,
                "release_gate": release_gate,
                "browser": request.browser.value,
                "check_code": evidence.code.value,
                "duration_ms": evidence.duration_ms,
            },
            observed_at=self.observed_at,
            created_at=self.observed_at,
        )
        _save_idempotent(self.store, event)
        _save_idempotent(self.store, decision)
        _save_idempotent(self.store, outcome)
        return ToolExecutionResult.succeeded(
            outcome.id,
            response_hash=_response_hash(outcome),
        )


def _save_idempotent(store: SQLiteExperienceStore, record):
    try:
        return store.save(record)
    except DuplicateRecordError:
        existing = store.get(record.id)
        if existing != record:
            raise
        return existing


def _response_hash(outcome: Outcome) -> str:
    payload = json.dumps(
        {
            "outcome_id": outcome.id,
            "status": outcome.status.value,
            "impact": outcome.impact,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"
