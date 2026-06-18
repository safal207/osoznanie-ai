"""SQLite persistence for immutable action-attempt evidence chains."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from .action_attempt import (
    ActionAttempt,
    ActionAttemptContractError,
    ActionAttemptStatus,
    build_failed_attempt,
    build_started_attempt,
    build_succeeded_attempt,
    hash_lease_token,
)
from .action_outbox import ActionIntent
from .models import Outcome
from .sqlite_action_outbox import SQLiteActionOutbox
from .storage import (
    STORAGE_RECORD_MODELS,
    DuplicateRecordError,
    MissingReferenceError,
    SQLiteExperienceStore,
    StorageError,
)

STORAGE_RECORD_MODELS["action_attempt"] = ActionAttempt


class ActionAttemptStorageError(StorageError):
    """Base exception for invalid action-attempt persistence."""


class InvalidActionAttemptProgressionError(ActionAttemptStorageError):
    """An attempt revision changed immutable dispatch context or skipped state."""


class SQLiteActionAttemptStore:
    """Persist started and terminal attempt revisions as immutable audit records."""

    def __init__(
        self,
        store: SQLiteExperienceStore,
        outbox: SQLiteActionOutbox | None = None,
    ) -> None:
        self.store = store
        self.outbox = outbox or SQLiteActionOutbox(store)
        self.initialize()

    def initialize(self) -> None:
        with self.store._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS action_attempt_index (
                    attempt_id TEXT PRIMARY KEY
                        REFERENCES records(id) ON DELETE RESTRICT,
                    intent_id TEXT NOT NULL
                        REFERENCES action_outbox(id) ON DELETE RESTRICT,
                    attempt_number INTEGER NOT NULL CHECK(attempt_number >= 1),
                    revision INTEGER NOT NULL CHECK(revision IN (1, 2)),
                    status TEXT NOT NULL CHECK(
                        status IN ('started', 'succeeded', 'failed')
                    ),
                    supersedes_attempt_id TEXT
                        REFERENCES records(id) ON DELETE RESTRICT,
                    started_at TEXT NOT NULL,
                    UNIQUE(intent_id, attempt_number, revision),
                    UNIQUE(supersedes_attempt_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_action_attempt_order
                ON action_attempt_index(
                    started_at, intent_id, attempt_number, revision, attempt_id
                )
                """
            )

    def start(
        self,
        intent: ActionIntent,
        worker_id: str,
        lease_token: str,
        started_at: datetime,
    ) -> ActionAttempt:
        attempt = build_started_attempt(intent, worker_id, lease_token, started_at)
        return self.save(attempt)

    def succeed(
        self,
        started_attempt: ActionAttempt,
        finished_at: datetime,
        outcome_id: str,
        *,
        response_hash: str | None = None,
    ) -> ActionAttempt:
        attempt = build_succeeded_attempt(
            started_attempt,
            finished_at,
            outcome_id,
            response_hash=response_hash,
        )
        return self.save(attempt)

    def fail(
        self,
        started_attempt: ActionAttempt,
        finished_at: datetime,
        error_code: str,
        *,
        response_hash: str | None = None,
    ) -> ActionAttempt:
        attempt = build_failed_attempt(
            started_attempt,
            finished_at,
            error_code,
            response_hash=response_hash,
        )
        return self.save(attempt)

    def save(self, attempt: ActionAttempt) -> ActionAttempt:
        expected_id = ActionAttempt.derive_id(attempt.canonical_payload())
        if attempt.id != expected_id:
            raise ActionAttemptContractError(
                "action-attempt id must match its canonical immutable payload"
            )

        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if self.store._record_exists(connection, attempt.id):
                    stored = self.store._load_record(connection, attempt.id)
                    if stored == attempt:
                        connection.execute("COMMIT")
                        return attempt
                    raise DuplicateRecordError(
                        f"Record already exists with different payload: {attempt.id}"
                    )

                missing = self.store._missing_references(connection, attempt)
                if missing:
                    raise MissingReferenceError(
                        f"Cannot save {attempt.id}; missing referenced records: "
                        f"{', '.join(missing)}"
                    )

                self._validate_intent_and_progression(connection, attempt)
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

                connection.execute("COMMIT")
                return attempt
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def get(self, attempt_id: str) -> ActionAttempt:
        record = self.store.get(attempt_id)
        if not isinstance(record, ActionAttempt):
            raise ActionAttemptStorageError(
                f"record is not an action attempt: {attempt_id}"
            )
        return record

    def list(self, intent_id: str | None = None) -> list[ActionAttempt]:
        query = "SELECT attempt_id FROM action_attempt_index"
        parameters: tuple[str, ...] = ()
        if intent_id is not None:
            normalized = intent_id.strip()
            if not normalized:
                raise ValueError("intent_id must not be blank")
            query += " WHERE intent_id = ?"
            parameters = (normalized,)
        query += (
            " ORDER BY started_at, intent_id, attempt_number, revision, attempt_id"
        )
        with self.store._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
            return [
                self._load_attempt(connection, row["attempt_id"])
                for row in rows
            ]

    def _validate_intent_and_progression(
        self,
        connection: sqlite3.Connection,
        attempt: ActionAttempt,
    ) -> None:
        intent_row = connection.execute(
            "SELECT * FROM action_outbox WHERE id = ?",
            (attempt.intent_id,),
        ).fetchone()
        if intent_row is None:
            raise MissingReferenceError(
                f"Cannot save {attempt.id}; missing action intent: {attempt.intent_id}"
            )

        expected_context = (
            intent_row["tool_name"],
            intent_row["tool_call_id"],
            intent_row["idempotency_key"],
            intent_row["input_hash"],
        )
        actual_context = (
            attempt.tool_name,
            attempt.tool_call_id,
            attempt.idempotency_key,
            attempt.input_hash,
        )
        if actual_context != expected_context:
            raise ActionAttemptContractError(
                "action-attempt dispatch metadata must match its action intent"
            )

        if attempt.revision == 1:
            if intent_row["status"] != "leased":
                raise ActionAttemptContractError(
                    "started attempts require a currently leased action intent"
                )
            if attempt.attempt_number != intent_row["attempt_count"]:
                raise ActionAttemptContractError(
                    "attempt_number must match the leased intent attempt_count"
                )
            if attempt.worker_id != intent_row["lease_owner"]:
                raise ActionAttemptContractError(
                    "worker_id must match the current lease owner"
                )
            lease_token = intent_row["lease_token"]
            if lease_token is None or attempt.lease_token_hash != hash_lease_token(
                lease_token
            ):
                raise ActionAttemptContractError(
                    "lease_token_hash must match the current lease"
                )
            claimed_at = datetime.fromisoformat(intent_row["updated_at"])
            if attempt.started_at < claimed_at:
                raise ActionAttemptContractError(
                    "started_at cannot be earlier than the lease claim"
                )
            expires_at = intent_row["lease_expires_at"]
            if expires_at is None or datetime.fromisoformat(expires_at) <= attempt.started_at:
                raise ActionAttemptContractError(
                    "started attempts require an unexpired lease"
                )
            return

        previous = self.store._load_record(
            connection,
            attempt.supersedes_attempt_id or "",
        )
        if not isinstance(previous, ActionAttempt):
            raise InvalidActionAttemptProgressionError(
                "supersedes_attempt_id must reference an ActionAttempt"
            )
        if previous.revision != 1 or previous.status is not ActionAttemptStatus.STARTED:
            raise InvalidActionAttemptProgressionError(
                "terminal attempt revisions must supersede a started record"
            )
        if attempt.revision != previous.revision + 1:
            raise InvalidActionAttemptProgressionError(
                "action-attempt revision must advance exactly by one"
            )
        if self._immutable_context(attempt) != self._immutable_context(previous):
            raise InvalidActionAttemptProgressionError(
                "terminal attempt revision changed immutable dispatch context"
            )
        if attempt.status is ActionAttemptStatus.SUCCEEDED:
            outcome = self.store._load_record(connection, attempt.outcome_id or "")
            if not isinstance(outcome, Outcome):
                raise InvalidActionAttemptProgressionError(
                    "succeeded action attempts must reference an Outcome"
                )

    @staticmethod
    def _immutable_context(attempt: ActionAttempt) -> tuple[object, ...]:
        return (
            attempt.intent_id,
            attempt.attempt_number,
            attempt.worker_id,
            attempt.lease_token_hash,
            attempt.tool_name,
            attempt.tool_call_id,
            attempt.idempotency_key,
            attempt.input_hash,
            attempt.started_at,
        )

    def _load_attempt(
        self,
        connection: sqlite3.Connection,
        attempt_id: str,
    ) -> ActionAttempt:
        record = self.store._load_record(connection, attempt_id)
        if not isinstance(record, ActionAttempt):
            raise ActionAttemptStorageError(
                f"record is not an action attempt: {attempt_id}"
            )
        return record
