"""SQLite persistence for immutable decision-trace chains."""

from __future__ import annotations

import sqlite3

from .decision_trace import DecisionTrace
from .storage import (
    STORAGE_RECORD_MODELS,
    DuplicateRecordError,
    MissingReferenceError,
    SQLiteExperienceStore,
    StorageError,
)

# Register the audit record with the generic loader once this module is imported.
STORAGE_RECORD_MODELS["decision_trace"] = DecisionTrace


class DecisionTraceStorageError(StorageError):
    """Base exception for invalid decision-trace persistence."""


class InvalidDecisionTraceProgressionError(DecisionTraceStorageError):
    """A superseding trace changed captured decision context or skipped a version."""


class SQLiteDecisionTraceStore:
    """Persist immutable traces and protect them with database foreign keys."""

    def __init__(self, store: SQLiteExperienceStore) -> None:
        self.store = store
        self.initialize()

    def initialize(self) -> None:
        with self.store._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS decision_trace_index (
                    trace_id TEXT PRIMARY KEY
                        REFERENCES records(id) ON DELETE RESTRICT,
                    trace_version INTEGER NOT NULL CHECK(trace_version >= 1),
                    supersedes_trace_id TEXT
                        REFERENCES records(id) ON DELETE RESTRICT,
                    UNIQUE(supersedes_trace_id)
                )
                """
            )

    def exists(self, trace_id: str) -> bool:
        """Return whether an immutable decision trace is already committed."""
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM decision_trace_index WHERE trace_id = ?",
                (trace_id,),
            ).fetchone()
        return row is not None

    def save(self, trace: DecisionTrace) -> DecisionTrace:
        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                persisted, _ = self._save_with_connection(connection, trace)
                connection.execute("COMMIT")
                return persisted
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def _save_with_connection(
        self,
        connection: sqlite3.Connection,
        trace: DecisionTrace,
    ) -> tuple[DecisionTrace, bool]:
        """Save inside an existing transaction and report whether it was inserted."""
        if self.store._record_exists(connection, trace.id):
            stored = self.store._load_record(connection, trace.id)
            if stored == trace:
                return trace, False
            raise DuplicateRecordError(
                f"Record already exists with different payload: {trace.id}"
            )

        missing = self.store._missing_references(connection, trace)
        if missing:
            raise MissingReferenceError(
                f"Cannot save {trace.id}; missing referenced records: "
                f"{', '.join(missing)}"
            )

        self._validate_progression(connection, trace)
        try:
            self.store._insert_record(connection, trace)
            connection.execute(
                """
                INSERT INTO decision_trace_index (
                    trace_id, trace_version, supersedes_trace_id
                ) VALUES (?, ?, ?)
                """,
                (
                    trace.id,
                    trace.trace_version,
                    trace.supersedes_trace_id,
                ),
            )
        except sqlite3.IntegrityError as error:
            raise DuplicateRecordError(
                "decision trace id or predecessor already belongs to "
                "another immutable audit node"
            ) from error
        return trace, True

    def get(self, trace_id: str) -> DecisionTrace:
        record = self.store.get(trace_id)
        if not isinstance(record, DecisionTrace):
            raise DecisionTraceStorageError(
                f"record is not a decision trace: {trace_id}"
            )
        return record

    def list(self) -> list[DecisionTrace]:
        records = self.store.list("decision_trace")
        return [record for record in records if isinstance(record, DecisionTrace)]

    def explain(self, trace_id: str) -> dict[str, object]:
        return self.store.explain(trace_id)

    def _validate_progression(
        self,
        connection: sqlite3.Connection,
        trace: DecisionTrace,
    ) -> None:
        if trace.trace_version == 1:
            if trace.supersedes_trace_id is not None:
                raise InvalidDecisionTraceProgressionError(
                    "trace version 1 cannot supersede another trace"
                )
            return

        if trace.supersedes_trace_id is None:
            raise InvalidDecisionTraceProgressionError(
                "trace versions after 1 require supersedes_trace_id"
            )
        previous = self.store._load_record(
            connection,
            trace.supersedes_trace_id,
        )
        if not isinstance(previous, DecisionTrace):
            raise InvalidDecisionTraceProgressionError(
                "supersedes_trace_id must reference a DecisionTrace"
            )
        if trace.trace_version != previous.trace_version + 1:
            raise InvalidDecisionTraceProgressionError(
                "decision trace version must advance exactly by one"
            )
        if trace.outcome_id is None:
            raise InvalidDecisionTraceProgressionError(
                "a superseding trace must attach an outcome in v0.1"
            )
        if self._captured_context(trace) != self._captured_context(previous):
            raise InvalidDecisionTraceProgressionError(
                "a superseding trace must preserve the captured decision context"
            )

    @staticmethod
    def _captured_context(trace: DecisionTrace) -> tuple[object, ...]:
        return (
            trace.requester_id,
            trace.agent_id,
            trace.action,
            trace.authorization_decision,
            tuple(trace.policy_memory_ids),
            tuple(trace.memory_ids),
            trace.as_of,
            trace.known_at,
            trace.decision_at,
            tuple(trace.alternatives_considered),
            tuple(trace.reason_codes),
            trace.tool_name,
            trace.tool_call_id,
            trace.input_hash,
        )
