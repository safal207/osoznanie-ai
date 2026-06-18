"""Atomic terminal action-attempt and outbox finalization."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .action_attempt import (
    ActionAttempt,
    ActionAttemptContractError,
    ActionAttemptStatus,
    build_failed_attempt,
    build_succeeded_attempt,
    hash_lease_token,
)
from .action_attempt_store import SQLiteActionAttemptStore
from .action_outbox import ActionIntentStatus
from .sqlite_action_outbox import SQLiteActionOutbox
from .storage import (
    DuplicateRecordError,
    MissingReferenceError,
    SQLiteExperienceStore,
)


class ActionFinalizationError(RuntimeError):
    """Base exception for terminal action finalization."""


class ActionFinalizationConflictError(ActionFinalizationError):
    """Persisted attempt evidence and outbox state disagree."""


class ActionFinalizationStatus(StrEnum):
    COMPLETED = "completed"
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED = "failed"


@dataclass(frozen=True)
class ActionFinalizationResult:
    attempt: ActionAttempt
    status: ActionFinalizationStatus
    already_finalized: bool
    retry_at: datetime | None = None


class SQLiteActionFinalizer:
    """Commit terminal evidence and queue state inside one BEGIN IMMEDIATE."""

    def __init__(
        self,
        store: SQLiteExperienceStore,
        outbox: SQLiteActionOutbox | None = None,
        attempt_store: SQLiteActionAttemptStore | None = None,
    ) -> None:
        self.store = store
        self.outbox = outbox or SQLiteActionOutbox(store)
        self.attempt_store = attempt_store or SQLiteActionAttemptStore(
            store,
            self.outbox,
        )
        self.initialize()

    def initialize(self) -> None:
        with self.store._connect() as connection:
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(action_outbox)"
                ).fetchall()
            }
            if "last_attempt_id" not in columns:
                connection.execute(
                    """
                    ALTER TABLE action_outbox
                    ADD COLUMN last_attempt_id TEXT
                        REFERENCES records(id) ON DELETE RESTRICT
                    """
                )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_action_outbox_last_attempt
                ON action_outbox(last_attempt_id)
                WHERE last_attempt_id IS NOT NULL
                """
            )

    def complete(
        self,
        started_attempt: ActionAttempt,
        lease_token: str,
        finished_at: datetime,
        outcome_id: str,
        *,
        response_hash: str | None = None,
    ) -> ActionFinalizationResult:
        terminal = build_succeeded_attempt(
            started_attempt,
            finished_at,
            outcome_id,
            response_hash=response_hash,
        )
        return self._finalize(
            started_attempt,
            terminal,
            lease_token,
            status=ActionFinalizationStatus.COMPLETED,
            retry_at=None,
        )

    def fail(
        self,
        started_attempt: ActionAttempt,
        lease_token: str,
        finished_at: datetime,
        error_code: str,
        *,
        retry_at: datetime | None = None,
        response_hash: str | None = None,
    ) -> ActionFinalizationResult:
        terminal = build_failed_attempt(
            started_attempt,
            finished_at,
            error_code,
            response_hash=response_hash,
        )
        status = (
            ActionFinalizationStatus.RETRY_SCHEDULED
            if retry_at is not None
            else ActionFinalizationStatus.FAILED
        )
        if retry_at is not None:
            retry_at = retry_at.astimezone(finished_at.tzinfo)
            if retry_at < finished_at:
                raise ValueError("retry_at must not be earlier than finished_at")
        return self._finalize(
            started_attempt,
            terminal,
            lease_token,
            status=status,
            retry_at=retry_at,
        )

    def get_last_attempt_id(self, intent_id: str) -> str | None:
        normalized = intent_id.strip()
        if not normalized:
            raise ValueError("intent_id must not be blank")
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT last_attempt_id FROM action_outbox WHERE id = ?",
                (normalized,),
            ).fetchone()
            if row is None:
                raise MissingReferenceError(f"missing action intent: {normalized}")
            return row["last_attempt_id"]

    def _finalize(
        self,
        started_attempt: ActionAttempt,
        terminal: ActionAttempt,
        lease_token: str,
        *,
        status: ActionFinalizationStatus,
        retry_at: datetime | None,
    ) -> ActionFinalizationResult:
        normalized_token = lease_token.strip()
        if not normalized_token:
            raise ValueError("lease_token must not be blank")

        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._load_intent_row(connection, started_attempt.intent_id)
                if self.store._record_exists(connection, terminal.id):
                    stored = self.store._load_record(connection, terminal.id)
                    if stored != terminal:
                        raise ActionFinalizationConflictError(
                            "terminal attempt id has conflicting content"
                        )
                    self._assert_committed_state(
                        row,
                        terminal,
                        normalized_token,
                        status,
                        retry_at,
                    )
                    connection.execute("COMMIT")
                    return ActionFinalizationResult(
                        attempt=terminal,
                        status=status,
                        already_finalized=True,
                        retry_at=retry_at,
                    )

                self._require_live_lease(
                    row,
                    started_attempt,
                    normalized_token,
                    terminal.finished_at,
                )
                self._save_attempt_with_connection(connection, terminal)
                self._after_attempt_saved(connection, terminal)
                self._transition_outbox(
                    connection,
                    row,
                    terminal,
                    status,
                    retry_at,
                )
                connection.execute("COMMIT")
                return ActionFinalizationResult(
                    attempt=terminal,
                    status=status,
                    already_finalized=False,
                    retry_at=retry_at,
                )
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def _save_attempt_with_connection(
        self,
        connection: sqlite3.Connection,
        attempt: ActionAttempt,
    ) -> None:
        expected_id = ActionAttempt.derive_id(attempt.canonical_payload())
        if attempt.id != expected_id:
            raise ActionAttemptContractError(
                "action-attempt id must match its canonical immutable payload"
            )
        missing = self.store._missing_references(connection, attempt)
        if missing:
            raise MissingReferenceError(
                f"Cannot save {attempt.id}; missing referenced records: "
                f"{', '.join(missing)}"
            )
        self.attempt_store._validate_intent_and_progression(connection, attempt)
        try:
            self.store._insert_record(connection, attempt)
            connection.execute(
                """
                INSERT INTO action_attempt_index (
                    attempt_id, intent_id, attempt_number, revision,
                    status, supersedes_attempt_id, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.id,
                    attempt.intent_id,
                    attempt.attempt_number,
                    attempt.revision,
                    attempt.status.value,
                    attempt.supersedes_attempt_id,
                    attempt.started_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise DuplicateRecordError(
                "action attempt id, revision, or predecessor already exists"
            ) from error

    def _transition_outbox(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        terminal: ActionAttempt,
        status: ActionFinalizationStatus,
        retry_at: datetime | None,
    ) -> None:
        finished = terminal.finished_at
        if finished is None:
            raise ActionFinalizationConflictError(
                "terminal attempt must contain finished_at"
            )
        if status is ActionFinalizationStatus.COMPLETED:
            connection.execute(
                """
                UPDATE action_outbox
                SET status = 'completed',
                    outcome_id = ?,
                    lease_owner = NULL,
                    lease_token = NULL,
                    lease_expires_at = NULL,
                    last_attempt_id = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    terminal.outcome_id,
                    terminal.id,
                    finished.isoformat(),
                    terminal.intent_id,
                ),
            )
            return

        next_status = (
            ActionIntentStatus.PENDING.value
            if status is ActionFinalizationStatus.RETRY_SCHEDULED
            else ActionIntentStatus.FAILED.value
        )
        available_at = (
            retry_at.isoformat()
            if retry_at is not None
            else row["available_at"]
        )
        connection.execute(
            """
            UPDATE action_outbox
            SET status = ?,
                available_at = ?,
                lease_owner = NULL,
                lease_token = NULL,
                lease_expires_at = NULL,
                last_error_code = ?,
                last_attempt_id = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                next_status,
                available_at,
                terminal.error_code,
                terminal.id,
                finished.isoformat(),
                terminal.intent_id,
            ),
        )

    def _assert_committed_state(
        self,
        row: sqlite3.Row,
        terminal: ActionAttempt,
        lease_token: str,
        status: ActionFinalizationStatus,
        retry_at: datetime | None,
    ) -> None:
        if terminal.lease_token_hash != hash_lease_token(lease_token):
            raise ActionFinalizationConflictError(
                "retry lease token does not match terminal evidence"
            )
        expected_status = {
            ActionFinalizationStatus.COMPLETED: "completed",
            ActionFinalizationStatus.RETRY_SCHEDULED: "pending",
            ActionFinalizationStatus.FAILED: "failed",
        }[status]
        if row["status"] != expected_status or row["last_attempt_id"] != terminal.id:
            raise ActionFinalizationConflictError(
                "outbox state does not match persisted terminal evidence"
            )
        if status is ActionFinalizationStatus.COMPLETED:
            if row["outcome_id"] != terminal.outcome_id:
                raise ActionFinalizationConflictError(
                    "completed outbox outcome does not match terminal evidence"
                )
        else:
            if row["last_error_code"] != terminal.error_code:
                raise ActionFinalizationConflictError(
                    "outbox error does not match terminal evidence"
                )
            if retry_at is not None and row["available_at"] != retry_at.isoformat():
                raise ActionFinalizationConflictError(
                    "outbox retry time does not match terminal evidence"
                )

    @staticmethod
    def _require_live_lease(
        row: sqlite3.Row,
        started_attempt: ActionAttempt,
        lease_token: str,
        finished_at: datetime | None,
    ) -> None:
        if finished_at is None:
            raise ActionFinalizationConflictError(
                "terminal attempt must contain finished_at"
            )
        if row["status"] != "leased":
            raise ActionFinalizationConflictError(
                "action intent is not currently leased"
            )
        if (
            row["lease_token"] != lease_token
            or started_attempt.lease_token_hash != hash_lease_token(lease_token)
        ):
            raise ActionFinalizationConflictError(
                "lease token does not match the active attempt"
            )
        if row["lease_owner"] != started_attempt.worker_id:
            raise ActionFinalizationConflictError(
                "lease owner does not match the active attempt"
            )
        if row["attempt_count"] != started_attempt.attempt_number:
            raise ActionFinalizationConflictError(
                "attempt number does not match the current lease"
            )
        expires_at = row["lease_expires_at"]
        if expires_at is None or datetime.fromisoformat(expires_at) <= finished_at:
            raise ActionFinalizationConflictError(
                "cannot finalize with an expired lease"
            )

    @staticmethod
    def _load_intent_row(
        connection: sqlite3.Connection,
        intent_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM action_outbox WHERE id = ?",
            (intent_id,),
        ).fetchone()
        if row is None:
            raise MissingReferenceError(f"missing action intent: {intent_id}")
        return row

    def _after_attempt_saved(
        self,
        connection: sqlite3.Connection,
        attempt: ActionAttempt,
    ) -> None:
        del connection, attempt
