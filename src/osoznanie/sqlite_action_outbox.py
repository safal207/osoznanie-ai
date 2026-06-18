"""SQLite transactional outbox for audited external action dispatch."""

from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timedelta

from .action_outbox import (
    ActionIntent,
    ActionIntentContractError,
    ActionIntentNotFoundError,
    ActionIntentProposal,
    ActionIntentStatus,
    LeaseConflictError,
    OutboxIdempotencyConflictError,
    TerminalActionIntentError,
    aware_utc,
    build_action_intent,
)
from .decision_trace import DecisionTrace
from .decision_trace_store import SQLiteDecisionTraceStore
from .storage import MissingReferenceError, SQLiteExperienceStore


class SQLiteActionOutbox:
    """Atomically persist traces and lease safe action intents to workers."""

    def __init__(
        self,
        store: SQLiteExperienceStore,
        trace_store: SQLiteDecisionTraceStore | None = None,
    ) -> None:
        self.store = store
        self.trace_store = trace_store or SQLiteDecisionTraceStore(store)
        self.initialize()

    def initialize(self) -> None:
        with self.store._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS action_outbox (
                    id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL UNIQUE
                        REFERENCES decision_trace_index(trace_id) ON DELETE RESTRICT,
                    requester_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    tool_call_id TEXT,
                    input_hash TEXT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL CHECK(
                        status IN ('pending', 'leased', 'completed', 'failed')
                    ),
                    attempt_count INTEGER NOT NULL DEFAULT 0
                        CHECK(attempt_count >= 0),
                    available_at TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_token TEXT,
                    lease_expires_at TEXT,
                    last_error_code TEXT,
                    outcome_id TEXT REFERENCES records(id) ON DELETE RESTRICT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_action_outbox_ready
                ON action_outbox(status, available_at, created_at, id)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_action_outbox_lease
                ON action_outbox(status, lease_expires_at)
                """
            )

    def enqueue(
        self,
        trace: DecisionTrace,
        proposal: ActionIntentProposal,
        *,
        available_at: datetime | None = None,
    ) -> ActionIntent:
        """Commit trace v1 and its action intent in one SQLite transaction."""
        intent = build_action_intent(
            trace,
            proposal,
            available_at=available_at,
        )
        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self.trace_store._save_with_connection(connection, trace)
                existing = self._find_existing(
                    connection,
                    intent.id,
                    intent.trace_id,
                )
                if existing is not None:
                    if existing.immutable_payload() != intent.immutable_payload():
                        raise OutboxIdempotencyConflictError(
                            "trace already owns a different action intent"
                        )
                    connection.execute("COMMIT")
                    return existing

                try:
                    self._insert_intent(connection, intent)
                except sqlite3.IntegrityError as error:
                    raise OutboxIdempotencyConflictError(
                        "action intent identity conflicts with persisted content"
                    ) from error
                connection.execute("COMMIT")
                return intent
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def get(self, intent_id: str) -> ActionIntent:
        with self.store._connect() as connection:
            return self._load_intent(connection, intent_id)

    def list_ready(self, now: datetime) -> list[ActionIntent]:
        current = aware_utc(now, field_name="now")
        with self.store._connect() as connection:
            rows = connection.execute(
                """
                SELECT id
                FROM action_outbox
                WHERE (
                    status = 'pending' AND available_at <= ?
                ) OR (
                    status = 'leased' AND lease_expires_at <= ?
                )
                ORDER BY available_at, created_at, id
                """,
                (current.isoformat(), current.isoformat()),
            ).fetchall()
            return [self._load_intent(connection, row["id"]) for row in rows]

    def claim(
        self,
        worker_id: str,
        now: datetime,
        lease_for: timedelta,
    ) -> ActionIntent | None:
        owner = worker_id.strip()
        if not owner:
            raise ValueError("worker_id must not be blank")
        current = aware_utc(now, field_name="now")
        if lease_for.total_seconds() <= 0:
            raise ValueError("lease_for must be positive")
        expires_at = current + lease_for

        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT id
                    FROM action_outbox
                    WHERE (
                        status = 'pending' AND available_at <= ?
                    ) OR (
                        status = 'leased' AND lease_expires_at <= ?
                    )
                    ORDER BY available_at, created_at, id
                    LIMIT 1
                    """,
                    (current.isoformat(), current.isoformat()),
                ).fetchone()
                if row is None:
                    connection.execute("COMMIT")
                    return None

                token = secrets.token_hex(16)
                connection.execute(
                    """
                    UPDATE action_outbox
                    SET status = 'leased',
                        attempt_count = attempt_count + 1,
                        lease_owner = ?,
                        lease_token = ?,
                        lease_expires_at = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        owner,
                        token,
                        expires_at.isoformat(),
                        current.isoformat(),
                        row["id"],
                    ),
                )
                claimed = self._load_intent(connection, row["id"])
                connection.execute("COMMIT")
                return claimed
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def complete(
        self,
        intent_id: str,
        lease_token: str,
        outcome_id: str,
        completed_at: datetime,
    ) -> ActionIntent:
        completed = aware_utc(completed_at, field_name="completed_at")
        normalized_outcome = outcome_id.strip()
        if not normalized_outcome:
            raise ValueError("outcome_id must not be blank")

        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                intent = self._load_intent(connection, intent_id)
                self._require_active_lease(intent, lease_token, completed)
                if not self.store._record_exists(connection, normalized_outcome):
                    raise MissingReferenceError(
                        f"missing outcome record: {normalized_outcome}"
                    )
                connection.execute(
                    """
                    UPDATE action_outbox
                    SET status = 'completed',
                        outcome_id = ?,
                        lease_owner = NULL,
                        lease_token = NULL,
                        lease_expires_at = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        normalized_outcome,
                        completed.isoformat(),
                        intent_id,
                    ),
                )
                result = self._load_intent(connection, intent_id)
                connection.execute("COMMIT")
                return result
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def fail(
        self,
        intent_id: str,
        lease_token: str,
        error_code: str,
        failed_at: datetime,
        *,
        retry_at: datetime | None = None,
    ) -> ActionIntent:
        failed = aware_utc(failed_at, field_name="failed_at")
        normalized_error = error_code.strip()
        if not normalized_error:
            raise ValueError("error_code must not be blank")
        retry = None
        if retry_at is not None:
            retry = aware_utc(retry_at, field_name="retry_at")
            if retry < failed:
                raise ValueError("retry_at must not be earlier than failed_at")

        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                intent = self._load_intent(connection, intent_id)
                self._require_active_lease(intent, lease_token, failed)
                next_status = (
                    ActionIntentStatus.PENDING
                    if retry is not None
                    else ActionIntentStatus.FAILED
                )
                available = retry or intent.available_at
                connection.execute(
                    """
                    UPDATE action_outbox
                    SET status = ?,
                        available_at = ?,
                        lease_owner = NULL,
                        lease_token = NULL,
                        lease_expires_at = NULL,
                        last_error_code = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        next_status.value,
                        available.isoformat(),
                        normalized_error,
                        failed.isoformat(),
                        intent_id,
                    ),
                )
                result = self._load_intent(connection, intent_id)
                connection.execute("COMMIT")
                return result
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def _insert_intent(
        self,
        connection: sqlite3.Connection,
        intent: ActionIntent,
    ) -> None:
        connection.execute(
            """
            INSERT INTO action_outbox (
                id, trace_id, requester_id, agent_id, action,
                tool_name, tool_call_id, input_hash, idempotency_key,
                status, attempt_count, available_at, lease_owner,
                lease_token, lease_expires_at, last_error_code,
                outcome_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                intent.id,
                intent.trace_id,
                intent.requester_id,
                intent.agent_id,
                intent.action,
                intent.tool_name,
                intent.tool_call_id,
                intent.input_hash,
                intent.idempotency_key,
                intent.status.value,
                intent.attempt_count,
                intent.available_at.isoformat(),
                intent.lease_owner,
                intent.lease_token,
                (
                    intent.lease_expires_at.isoformat()
                    if intent.lease_expires_at is not None
                    else None
                ),
                intent.last_error_code,
                intent.outcome_id,
                intent.created_at.isoformat(),
                intent.updated_at.isoformat(),
            ),
        )

    def _find_existing(
        self,
        connection: sqlite3.Connection,
        intent_id: str,
        trace_id: str,
    ) -> ActionIntent | None:
        row = connection.execute(
            """
            SELECT id FROM action_outbox
            WHERE id = ? OR trace_id = ?
            LIMIT 1
            """,
            (intent_id, trace_id),
        ).fetchone()
        if row is None:
            return None
        return self._load_intent(connection, row["id"])

    @staticmethod
    def _require_active_lease(
        intent: ActionIntent,
        lease_token: str,
        at: datetime,
    ) -> None:
        if intent.status in (
            ActionIntentStatus.COMPLETED,
            ActionIntentStatus.FAILED,
        ):
            raise TerminalActionIntentError(
                f"action intent is terminal: {intent.status.value}"
            )
        normalized_token = lease_token.strip()
        if (
            intent.status is not ActionIntentStatus.LEASED
            or not normalized_token
            or normalized_token != intent.lease_token
            or intent.lease_expires_at is None
            or intent.lease_expires_at <= at
        ):
            raise LeaseConflictError("missing, stale, or expired action lease")

    @staticmethod
    def _load_intent(
        connection: sqlite3.Connection,
        intent_id: str,
    ) -> ActionIntent:
        row = connection.execute(
            "SELECT * FROM action_outbox WHERE id = ?",
            (intent_id,),
        ).fetchone()
        if row is None:
            raise ActionIntentNotFoundError(intent_id)
        return ActionIntent(
            id=row["id"],
            trace_id=row["trace_id"],
            requester_id=row["requester_id"],
            agent_id=row["agent_id"],
            action=row["action"],
            tool_name=row["tool_name"],
            tool_call_id=row["tool_call_id"],
            input_hash=row["input_hash"],
            idempotency_key=row["idempotency_key"],
            status=ActionIntentStatus(row["status"]),
            attempt_count=row["attempt_count"],
            available_at=datetime.fromisoformat(row["available_at"]),
            lease_owner=row["lease_owner"],
            lease_token=row["lease_token"],
            lease_expires_at=(
                datetime.fromisoformat(row["lease_expires_at"])
                if row["lease_expires_at"] is not None
                else None
            ),
            last_error_code=row["last_error_code"],
            outcome_id=row["outcome_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
