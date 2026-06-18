"""Append-only SQLite repository for lesson application contracts."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TypeVar

from .application import (
    APPLICATION_RECORD_MODELS,
    ApplicationRecord,
    CriterionEvaluation,
    LessonApplication,
    OutcomeObservation,
    SuccessCriterion,
)
from .models import Lesson, ProtocolRecord
from .storage import SQLiteExperienceStore, StorageError


class ApplicationStoreError(StorageError):
    """Base exception for application-record persistence failures."""


class ApplicationRecordNotFoundError(ApplicationStoreError):
    pass


class MissingApplicationReferenceError(ApplicationStoreError):
    pass


class ApplicationContractReferenceError(ApplicationStoreError):
    pass


class ApplicationTemporalContractError(ApplicationStoreError):
    pass


class ApplicationIdempotencyConflictError(ApplicationStoreError):
    pass


class DuplicateApplicationRecordError(ApplicationStoreError):
    pass


ExpectedApplication = TypeVar("ExpectedApplication", bound=ProtocolRecord)


class SQLiteApplicationStore:
    """Persist causal-discipline records beside an existing experience store.

    The repository uses the same SQLite connection boundary as
    :class:`SQLiteExperienceStore`, so application payload and idempotency-key
    inserts commit or roll back together. Existing experience records remain owned
    by the experience store and are resolved before application records are saved.
    """

    def __init__(self, experience_store: SQLiteExperienceStore) -> None:
        self.experience_store = experience_store
        self.initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with self.experience_store._connect() as connection:
            yield connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS application_records (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_application_records_type
                ON application_records(type)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS application_idempotency (
                    record_type TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    record_id TEXT NOT NULL REFERENCES application_records(id),
                    PRIMARY KEY (record_type, idempotency_key)
                )
                """
            )

    @staticmethod
    def _application_exists(
        connection: sqlite3.Connection,
        record_id: str,
    ) -> bool:
        row = connection.execute(
            "SELECT 1 FROM application_records WHERE id = ?",
            (record_id,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _load_application(
        connection: sqlite3.Connection,
        record_id: str,
    ) -> ApplicationRecord:
        row = connection.execute(
            "SELECT type, payload FROM application_records WHERE id = ?",
            (record_id,),
        ).fetchone()
        if row is None:
            raise ApplicationRecordNotFoundError(record_id)
        model = APPLICATION_RECORD_MODELS.get(row["type"])
        if model is None:
            raise ApplicationStoreError(
                f"Unknown application record type: {row['type']}"
            )
        return model.model_validate_json(row["payload"])  # type: ignore[return-value]

    def get(self, record_id: str) -> ApplicationRecord:
        with self._connect() as connection:
            return self._load_application(connection, record_id)

    def list(self, record_type: str | None = None) -> list[ApplicationRecord]:
        query = "SELECT id FROM application_records"
        parameters: tuple[str, ...] = ()
        if record_type is not None:
            if record_type not in APPLICATION_RECORD_MODELS:
                raise ValueError(f"Unknown application record type: {record_type}")
            query += " WHERE type = ?"
            parameters = (record_type,)
        query += " ORDER BY created_at, id"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
            return [self._load_application(connection, row["id"]) for row in rows]

    def _require_experience(
        self,
        connection: sqlite3.Connection,
        record_id: str,
        field_name: str,
    ) -> ProtocolRecord:
        try:
            return self.experience_store._load_record(connection, record_id)
        except Exception as error:
            raise MissingApplicationReferenceError(
                f"{field_name} references missing experience record: {record_id}"
            ) from error

    def _require_experience_type(
        self,
        connection: sqlite3.Connection,
        record_id: str,
        expected_type: type[ExpectedApplication],
        field_name: str,
    ) -> ExpectedApplication:
        record = self._require_experience(connection, record_id, field_name)
        if not isinstance(record, expected_type):
            raise ApplicationContractReferenceError(
                f"{field_name} must reference {expected_type.__name__}; "
                f"got {record.type}"
            )
        return record

    def _require_application_type(
        self,
        connection: sqlite3.Connection,
        record_id: str,
        expected_type: type[ExpectedApplication],
        field_name: str,
    ) -> ExpectedApplication:
        try:
            record = self._load_application(connection, record_id)
        except ApplicationRecordNotFoundError as error:
            raise MissingApplicationReferenceError(
                f"{field_name} references missing application record: {record_id}"
            ) from error
        if not isinstance(record, expected_type):
            raise ApplicationContractReferenceError(
                f"{field_name} must reference {expected_type.__name__}; "
                f"got {record.type}"
            )
        return record

    def _existing_idempotent_application(
        self,
        connection: sqlite3.Connection,
        record: LessonApplication,
    ) -> LessonApplication | None:
        row = connection.execute(
            """
            SELECT payload_hash, record_id
            FROM application_idempotency
            WHERE record_type = ? AND idempotency_key = ?
            """,
            (record.type, record.idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if row["payload_hash"] != record.idempotency_fingerprint():
            raise ApplicationIdempotencyConflictError(
                "idempotency key is already bound to a different application payload"
            )
        existing = self._load_application(connection, row["record_id"])
        if not isinstance(existing, LessonApplication):
            raise ApplicationStoreError(
                "idempotency key references a non-application record"
            )
        return existing

    def _validate_application(
        self,
        connection: sqlite3.Connection,
        record: LessonApplication,
    ) -> None:
        self._require_experience_type(
            connection,
            record.lesson_id,
            Lesson,
            "lesson_id",
        )
        criterion = self._require_application_type(
            connection,
            record.success_criterion_id,
            SuccessCriterion,
            "success_criterion_id",
        )
        query = self._require_experience(
            connection,
            record.recall_query_id,
            "recall_query_id",
        )
        retrieval = self._require_experience(
            connection,
            record.retrieval_execution_id,
            "retrieval_execution_id",
        )
        action = self._require_experience(
            connection,
            record.action_execution_id,
            "action_execution_id",
        )
        environment = self._require_experience(
            connection,
            record.environment_snapshot_id,
            "environment_snapshot_id",
        )

        if criterion.fixed_at > query.created_at:
            raise ApplicationTemporalContractError(
                "success criterion must be fixed before recall query creation"
            )
        if query.created_at > retrieval.created_at:
            raise ApplicationTemporalContractError(
                "recall query must exist before retrieval execution"
            )
        if retrieval.created_at > record.applied_at:
            raise ApplicationTemporalContractError(
                "retrieval execution must complete before lesson application"
            )
        if action.created_at > record.applied_at:
            raise ApplicationTemporalContractError(
                "action execution record must not postdate lesson application"
            )
        if environment.created_at > record.applied_at:
            raise ApplicationTemporalContractError(
                "environment snapshot must not postdate lesson application"
            )
        if record.environment_projection_id is not None:
            projection = self._require_experience(
                connection,
                record.environment_projection_id,
                "environment_projection_id",
            )
            if projection.created_at > record.applied_at:
                raise ApplicationTemporalContractError(
                    "environment projection must not postdate lesson application"
                )

    def _validate_observation(
        self,
        connection: sqlite3.Connection,
        record: OutcomeObservation,
    ) -> None:
        applications = [
            self._require_application_type(
                connection,
                application_id,
                LessonApplication,
                "lesson_application_ids",
            )
            for application_id in record.lesson_application_ids
        ]
        self._require_experience(
            connection,
            record.action_execution_id,
            "action_execution_id",
        )
        for source_id in record.source_event_ids:
            self._require_experience(connection, source_id, "source_event_ids")
        for evidence_id in record.evidence_ids:
            self._require_experience(connection, evidence_id, "evidence_ids")

        for application in applications:
            if application.action_execution_id != record.action_execution_id:
                raise ApplicationContractReferenceError(
                    "observation action must match every lesson application action"
                )
            if application.applied_at > record.observed_at:
                raise ApplicationTemporalContractError(
                    "outcome observation must not predate lesson application"
                )

        if record.supersedes_observation_id is not None:
            previous = self._require_application_type(
                connection,
                record.supersedes_observation_id,
                OutcomeObservation,
                "supersedes_observation_id",
            )
            if previous.action_execution_id != record.action_execution_id:
                raise ApplicationContractReferenceError(
                    "corrected observation must reference the same action execution"
                )
            if previous.observed_at > record.observed_at:
                raise ApplicationTemporalContractError(
                    "corrected observation must not predate superseded observation"
                )

    def _validate_evaluation(
        self,
        connection: sqlite3.Connection,
        record: CriterionEvaluation,
    ) -> None:
        self._require_application_type(
            connection,
            record.criterion_id,
            SuccessCriterion,
            "criterion_id",
        )
        applications = [
            self._require_application_type(
                connection,
                application_id,
                LessonApplication,
                "lesson_application_ids",
            )
            for application_id in record.lesson_application_ids
        ]
        for application in applications:
            if application.success_criterion_id != record.criterion_id:
                raise ApplicationContractReferenceError(
                    "evaluation criterion must match every lesson application"
                )

        observations = [
            self._require_application_type(
                connection,
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
            raise ApplicationContractReferenceError(
                "referenced observations do not cover every evaluated application"
            )
        if any(
            observation.observed_at > record.evaluated_at
            for observation in observations
        ):
            raise ApplicationTemporalContractError(
                "criterion evaluation must not predate referenced observations"
            )

        if record.supersedes_evaluation_id is not None:
            previous = self._require_application_type(
                connection,
                record.supersedes_evaluation_id,
                CriterionEvaluation,
                "supersedes_evaluation_id",
            )
            if previous.criterion_id != record.criterion_id:
                raise ApplicationContractReferenceError(
                    "corrected evaluation must reference the same criterion"
                )
            if previous.lesson_application_ids != record.lesson_application_ids:
                raise ApplicationContractReferenceError(
                    "corrected evaluation must cover the same applications"
                )
            if previous.evaluated_at > record.evaluated_at:
                raise ApplicationTemporalContractError(
                    "corrected evaluation must not predate superseded evaluation"
                )

    def _validate_contract(
        self,
        connection: sqlite3.Connection,
        record: ApplicationRecord,
    ) -> None:
        if isinstance(record, LessonApplication):
            self._validate_application(connection, record)
        elif isinstance(record, OutcomeObservation):
            self._validate_observation(connection, record)
        elif isinstance(record, CriterionEvaluation):
            self._validate_evaluation(connection, record)

    def save(self, record: ApplicationRecord) -> ApplicationRecord:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if isinstance(record, LessonApplication):
                    existing = self._existing_idempotent_application(
                        connection,
                        record,
                    )
                    if existing is not None:
                        connection.execute("COMMIT")
                        return existing

                self._validate_contract(connection, record)
                connection.execute(
                    """
                    INSERT INTO application_records (id, type, payload, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.type,
                        record.canonical_json(),
                        record.created_at.isoformat(),
                    ),
                )
                if isinstance(record, LessonApplication):
                    connection.execute(
                        """
                        INSERT INTO application_idempotency (
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
                connection.execute("COMMIT")
                return record
            except sqlite3.IntegrityError as error:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                if isinstance(record, LessonApplication):
                    raise ApplicationIdempotencyConflictError(
                        "lesson application id or idempotency key already exists"
                    ) from error
                raise DuplicateApplicationRecordError(
                    f"Application record already exists: {record.id}"
                ) from error
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
