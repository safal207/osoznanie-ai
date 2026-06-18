"""Minimal SQLite persistence with provenance and causal-contract validation."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

from .application import (
    APPLICATION_RECORD_MODELS,
    ApplicationRecord,
    CriterionEvaluation,
    LessonApplication,
    OutcomeObservation,
    SuccessCriterion,
)
from .memory import MemoryObject
from .models import Lesson, ProtocolRecord, RECORD_MODELS, Record


class StorageError(RuntimeError):
    """Base exception for storage failures."""


class RecordNotFoundError(StorageError):
    pass


class DuplicateRecordError(StorageError):
    pass


class MissingReferenceError(StorageError):
    pass


class ReferencedRecordError(StorageError):
    pass


class ContractReferenceError(StorageError):
    pass


class TemporalContractError(StorageError):
    pass


class IdempotencyConflictError(StorageError):
    pass


StoredRecord = Record | MemoryObject | ApplicationRecord
STORAGE_RECORD_MODELS = {
    **RECORD_MODELS,
    "memory": MemoryObject,
    **APPLICATION_RECORD_MODELS,
}

ExpectedRecord = TypeVar("ExpectedRecord", bound=ProtocolRecord)


class SQLiteExperienceStore:
    """Store protocol records as validated, append-only JSON documents."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._memory_connection: sqlite3.Connection | None = None
        if self.path == ":memory:":
            self._memory_connection = self._new_connection()
        self.initialize()

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._memory_connection is not None:
            yield self._memory_connection
            return

        connection = self._new_connection()
        try:
            yield connection
        finally:
            connection.close()

    def close(self) -> None:
        if self._memory_connection is not None:
            self._memory_connection.close()
            self._memory_connection = None

    def __enter__(self) -> SQLiteExperienceStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS records (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_records_type ON records(type)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    record_type TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    PRIMARY KEY (record_type, idempotency_key),
                    FOREIGN KEY (record_id) REFERENCES records(id)
                )
                """
            )
            connection.commit()

    def exists(self, record_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM records WHERE id = ?",
                (record_id,),
            ).fetchone()
        return row is not None

    def _require_type(
        self,
        record_id: str,
        expected_type: type[ExpectedRecord],
        field_name: str,
    ) -> ExpectedRecord:
        record = self.get(record_id)
        if not isinstance(record, expected_type):
            raise ContractReferenceError(
                f"{field_name} must reference {expected_type.__name__}; "
                f"got {record.type}"
            )
        return record

    def _existing_idempotent_application(
        self,
        record: LessonApplication,
    ) -> LessonApplication | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_hash, record_id
                FROM idempotency_keys
                WHERE record_type = ? AND idempotency_key = ?
                """,
                (record.type, record.idempotency_key),
            ).fetchone()
        if row is None:
            return None
        if row["payload_hash"] != record.idempotency_fingerprint():
            raise IdempotencyConflictError(
                "idempotency key is already bound to a different application payload"
            )
        existing = self.get(row["record_id"])
        if not isinstance(existing, LessonApplication):
            raise StorageError("idempotency key references a non-application record")
        return existing

    def _validate_application(self, record: LessonApplication) -> None:
        self._require_type(record.lesson_id, Lesson, "lesson_id")
        criterion = self._require_type(
            record.success_criterion_id,
            SuccessCriterion,
            "success_criterion_id",
        )
        query = self.get(record.recall_query_id)
        retrieval = self.get(record.retrieval_execution_id)
        action = self.get(record.action_execution_id)
        environment = self.get(record.environment_snapshot_id)

        if criterion.fixed_at > query.created_at:
            raise TemporalContractError(
                "success criterion must be fixed before recall query creation"
            )
        if query.created_at > retrieval.created_at:
            raise TemporalContractError(
                "recall query must exist before retrieval execution"
            )
        if retrieval.created_at > record.applied_at:
            raise TemporalContractError(
                "retrieval execution must complete before lesson application"
            )
        if action.created_at > record.applied_at:
            raise TemporalContractError(
                "action execution record must not postdate lesson application"
            )
        if environment.created_at > record.applied_at:
            raise TemporalContractError(
                "environment snapshot must not postdate lesson application"
            )
        if record.environment_projection_id is not None:
            projection = self.get(record.environment_projection_id)
            if projection.created_at > record.applied_at:
                raise TemporalContractError(
                    "environment projection must not postdate lesson application"
                )

    def _validate_observation(self, record: OutcomeObservation) -> None:
        applications = [
            self._require_type(
                application_id,
                LessonApplication,
                "lesson_application_ids",
            )
            for application_id in record.lesson_application_ids
        ]
        for application in applications:
            if application.action_execution_id != record.action_execution_id:
                raise ContractReferenceError(
                    "observation action must match every lesson application action"
                )
            if application.applied_at > record.observed_at:
                raise TemporalContractError(
                    "outcome observation must not predate lesson application"
                )

        if record.supersedes_observation_id is not None:
            previous = self._require_type(
                record.supersedes_observation_id,
                OutcomeObservation,
                "supersedes_observation_id",
            )
            if previous.action_execution_id != record.action_execution_id:
                raise ContractReferenceError(
                    "corrected observation must reference the same action execution"
                )
            if previous.observed_at > record.observed_at:
                raise TemporalContractError(
                    "corrected observation must not predate superseded observation"
                )

    def _validate_evaluation(self, record: CriterionEvaluation) -> None:
        self._require_type(record.criterion_id, SuccessCriterion, "criterion_id")
        applications = [
            self._require_type(
                application_id,
                LessonApplication,
                "lesson_application_ids",
            )
            for application_id in record.lesson_application_ids
        ]
        for application in applications:
            if application.success_criterion_id != record.criterion_id:
                raise ContractReferenceError(
                    "evaluation criterion must match every lesson application"
                )

        observations = [
            self._require_type(
                observation_id,
                OutcomeObservation,
                "observation_ids",
            )
            for observation_id in record.observation_ids
        ]
        covered_application_ids = {
            application_id
            for observation in observations
            for application_id in observation.lesson_application_ids
        }
        if observations and not set(record.lesson_application_ids).issubset(
            covered_application_ids
        ):
            raise ContractReferenceError(
                "referenced observations do not cover every evaluated application"
            )
        if any(observation.observed_at > record.evaluated_at for observation in observations):
            raise TemporalContractError(
                "criterion evaluation must not predate referenced observations"
            )

        if record.supersedes_evaluation_id is not None:
            previous = self._require_type(
                record.supersedes_evaluation_id,
                CriterionEvaluation,
                "supersedes_evaluation_id",
            )
            if previous.criterion_id != record.criterion_id:
                raise ContractReferenceError(
                    "corrected evaluation must reference the same criterion"
                )
            if previous.lesson_application_ids != record.lesson_application_ids:
                raise ContractReferenceError(
                    "corrected evaluation must cover the same applications"
                )
            if previous.evaluated_at > record.evaluated_at:
                raise TemporalContractError(
                    "corrected evaluation must not predate superseded evaluation"
                )

    def _validate_contract(self, record: StoredRecord) -> None:
        if isinstance(record, LessonApplication):
            self._validate_application(record)
        elif isinstance(record, OutcomeObservation):
            self._validate_observation(record)
        elif isinstance(record, CriterionEvaluation):
            self._validate_evaluation(record)

    def save(self, record: StoredRecord) -> StoredRecord:
        if isinstance(record, LessonApplication):
            existing = self._existing_idempotent_application(record)
            if existing is not None:
                return existing

        missing = [
            reference
            for reference in record.reference_ids()
            if not self.exists(reference)
        ]
        if missing:
            raise MissingReferenceError(
                f"Cannot save {record.id}; missing referenced records: "
                f"{', '.join(missing)}"
            )

        self._validate_contract(record)

        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO records (id, type, payload, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.type,
                        record.model_dump_json(),
                        record.created_at.isoformat(),
                    ),
                )
                if isinstance(record, LessonApplication):
                    connection.execute(
                        """
                        INSERT INTO idempotency_keys (
                            record_type,
                            idempotency_key,
                            payload_hash,
                            record_id
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            record.type,
                            record.idempotency_key,
                            record.idempotency_fingerprint(),
                            record.id,
                        ),
                    )
                connection.commit()
        except sqlite3.IntegrityError as error:
            if isinstance(record, LessonApplication):
                raise IdempotencyConflictError(
                    "lesson application id or idempotency key already exists"
                ) from error
            raise DuplicateRecordError(f"Record already exists: {record.id}") from error

        return record

    def get(self, record_id: str) -> StoredRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT type, payload FROM records WHERE id = ?",
                (record_id,),
            ).fetchone()

        if row is None:
            raise RecordNotFoundError(record_id)

        model = STORAGE_RECORD_MODELS.get(row["type"])
        if model is None:
            raise StorageError(f"Unknown stored record type: {row['type']}")
        return model.model_validate_json(row["payload"])  # type: ignore[return-value]

    def list(self, record_type: str | None = None) -> list[StoredRecord]:
        query = "SELECT id FROM records"
        parameters: tuple[str, ...] = ()
        if record_type is not None:
            if record_type not in STORAGE_RECORD_MODELS:
                raise ValueError(f"Unknown record type: {record_type}")
            query += " WHERE type = ?"
            parameters = (record_type,)
        query += " ORDER BY created_at, id"

        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self.get(row["id"]) for row in rows]

    def delete(self, record_id: str, *, force: bool = False) -> None:
        if not self.exists(record_id):
            raise RecordNotFoundError(record_id)

        dependants = [
            record.id
            for record in self.list()
            if record.id != record_id and record_id in record.reference_ids()
        ]
        if dependants and not force:
            raise ReferencedRecordError(
                f"Record {record_id} is referenced by: {', '.join(dependants)}"
            )

        with self._connect() as connection:
            connection.execute(
                "DELETE FROM idempotency_keys WHERE record_id = ?",
                (record_id,),
            )
            connection.execute("DELETE FROM records WHERE id = ?", (record_id,))
            connection.commit()

    def explain(self, record_id: str) -> dict[str, object]:
        """Return an inspectable provenance view for one record."""

        record = self.get(record_id)
        references = [self.get(reference) for reference in record.reference_ids()]
        return {
            "record": record.model_dump(mode="json"),
            "references": [item.model_dump(mode="json") for item in references],
        }
