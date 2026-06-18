"""Application-aware extension of the atomic SQLite experience store."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import TypeAlias, TypeVar

from .application import (
    APPLICATION_RECORD_MODELS,
    ApplicationRecord,
    CriterionEvaluation,
    CriterionResult,
    EvaluationReasonCode,
    LessonApplication,
    OutcomeObservation,
    SuccessCriterion,
)
from .models import Event, Lesson, ProtocolRecord
from .storage import (
    STORAGE_RECORD_MODELS,
    DuplicateRecordError,
    MissingReferenceError,
    RecordNotFoundError,
    ReferencedRecordError,
    SQLiteExperienceStore,
    StorageError,
    StoredRecord,
)

ApplicationStoredRecord: TypeAlias = StoredRecord | ApplicationRecord
APPLICATION_STORAGE_RECORD_MODELS = {
    **STORAGE_RECORD_MODELS,
    **APPLICATION_RECORD_MODELS,
}
ExpectedRecord = TypeVar("ExpectedRecord", bound=ProtocolRecord)


class ContractReferenceError(StorageError):
    """Raised when a reference resolves to an incompatible record."""


class TemporalContractError(StorageError):
    """Raised when causal records violate required time ordering."""


class IdempotencyConflictError(StorageError):
    """Raised when one application key is reused for different semantics."""


class SQLiteApplicationStore(SQLiteExperienceStore):
    """Persist application records without changing memory-head semantics."""

    def initialize(self) -> None:
        super().initialize()
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS application_idempotency_keys (
                    record_type TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    record_id TEXT NOT NULL REFERENCES records(id),
                    PRIMARY KEY (record_type, idempotency_key)
                )
                """
            )

    @staticmethod
    def _load_record(
        connection: sqlite3.Connection,
        record_id: str,
    ) -> ApplicationStoredRecord:
        row = connection.execute(
            "SELECT type, payload FROM records WHERE id = ?",
            (record_id,),
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(record_id)
        model = APPLICATION_STORAGE_RECORD_MODELS.get(row["type"])
        if model is None:
            raise StorageError(f"Unknown stored record type: {row['type']}")
        return model.model_validate_json(row["payload"])  # type: ignore[return-value]

    @classmethod
    def _require_type(
        cls,
        connection: sqlite3.Connection,
        record_id: str,
        expected_type: type[ExpectedRecord],
        field_name: str,
    ) -> ExpectedRecord:
        record = cls._load_record(connection, record_id)
        if not isinstance(record, expected_type):
            raise ContractReferenceError(
                f"{field_name} must reference {expected_type.__name__}; got {record.type}"
            )
        return record

    @staticmethod
    def _missing_references(
        connection: sqlite3.Connection,
        record: ApplicationRecord,
    ) -> list[str]:
        return [
            reference
            for reference in record.reference_ids()
            if not SQLiteApplicationStore._record_exists(connection, reference)
        ]

    @classmethod
    def _idempotent_replay(
        cls,
        connection: sqlite3.Connection,
        record: LessonApplication,
    ) -> LessonApplication | None:
        row = connection.execute(
            """
            SELECT payload_hash, record_id
            FROM application_idempotency_keys
            WHERE record_type = ? AND idempotency_key = ?
            """,
            (record.type, record.idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if row["payload_hash"] != record.idempotency_fingerprint():
            raise IdempotencyConflictError(
                "idempotency key is bound to a different application payload"
            )
        existing = cls._load_record(connection, row["record_id"])
        if not isinstance(existing, LessonApplication):
            raise StorageError("idempotency key points to a non-application record")
        return existing

    @classmethod
    def _validate_application(
        cls,
        connection: sqlite3.Connection,
        record: LessonApplication,
    ) -> None:
        cls._require_type(connection, record.lesson_id, Lesson, "lesson_id")
        criterion = cls._require_type(
            connection,
            record.success_criterion_id,
            SuccessCriterion,
            "success_criterion_id",
        )
        query = cls._require_type(connection, record.recall_query_id, Event, "recall_query_id")
        retrieval = cls._require_type(
            connection,
            record.retrieval_execution_id,
            Event,
            "retrieval_execution_id",
        )
        action = cls._require_type(
            connection,
            record.action_execution_id,
            Event,
            "action_execution_id",
        )
        environment = cls._require_type(
            connection,
            record.environment_snapshot_id,
            Event,
            "environment_snapshot_id",
        )
        if criterion.fixed_at > query.timestamp:
            raise TemporalContractError("criterion must be fixed before query creation")
        if query.timestamp > retrieval.timestamp:
            raise TemporalContractError("query must precede retrieval execution")
        if retrieval.timestamp > record.applied_at:
            raise TemporalContractError("retrieval must precede application")
        if action.timestamp > record.applied_at:
            raise TemporalContractError("action must not postdate application")
        if environment.timestamp > record.applied_at:
            raise TemporalContractError("environment snapshot must not postdate application")
        if record.environment_projection_id is not None:
            projection = cls._require_type(
                connection,
                record.environment_projection_id,
                Event,
                "environment_projection_id",
            )
            if projection.timestamp > record.applied_at:
                raise TemporalContractError("environment projection must not postdate application")

    @classmethod
    def _validate_observation(
        cls,
        connection: sqlite3.Connection,
        record: OutcomeObservation,
    ) -> None:
        cls._require_type(connection, record.action_execution_id, Event, "action_execution_id")
        for application_id in record.lesson_application_ids:
            application = cls._require_type(
                connection,
                application_id,
                LessonApplication,
                "lesson_application_ids",
            )
            if application.action_execution_id != record.action_execution_id:
                raise ContractReferenceError("observation and application actions must match")
            if application.applied_at > record.observed_at:
                raise TemporalContractError("observation must not predate application")
        if record.supersedes_observation_id is not None:
            previous = cls._require_type(
                connection,
                record.supersedes_observation_id,
                OutcomeObservation,
                "supersedes_observation_id",
            )
            if previous.action_execution_id != record.action_execution_id:
                raise ContractReferenceError("corrected observation must keep the same action")
            if previous.observed_at > record.observed_at:
                raise TemporalContractError("correction must not predate superseded observation")

    @classmethod
    def _validate_evaluation(
        cls,
        connection: sqlite3.Connection,
        record: CriterionEvaluation,
    ) -> None:
        criterion = cls._require_type(
            connection,
            record.criterion_id,
            SuccessCriterion,
            "criterion_id",
        )
        if criterion.evaluator_version != record.evaluator_version:
            raise ContractReferenceError("evaluation version must match fixed criterion")
        applications = [
            cls._require_type(
                connection,
                application_id,
                LessonApplication,
                "lesson_application_ids",
            )
            for application_id in record.lesson_application_ids
        ]
        if any(app.success_criterion_id != criterion.id for app in applications):
            raise ContractReferenceError("evaluation criterion must match every application")
        observations = [
            cls._require_type(
                connection,
                observation_id,
                OutcomeObservation,
                "observation_ids",
            )
            for observation_id in record.observation_ids
        ]
        covered = {
            application_id
            for observation in observations
            for application_id in observation.lesson_application_ids
        }
        if observations and not set(record.lesson_application_ids).issubset(covered):
            raise ContractReferenceError("observations must cover every evaluated application")
        if any(obs.observed_at > record.evaluated_at for obs in observations):
            raise TemporalContractError("evaluation must not predate observations")
        metric_observed = any(
            any(value.key == criterion.metric_key for value in observation.values)
            for observation in observations
        )
        if record.result is not CriterionResult.INDETERMINATE and not metric_observed:
            raise ContractReferenceError("determinate evaluation requires criterion metric")
        deadlines = {
            app.id: app.applied_at + timedelta(seconds=criterion.observation_window_seconds)
            for app in applications
        }
        late = any(
            observation.observed_at > deadlines[application_id]
            for observation in observations
            for application_id in observation.lesson_application_ids
            if application_id in deadlines
        )
        if record.result is not CriterionResult.INDETERMINATE and late:
            raise TemporalContractError("determinate evaluation used a late observation")
        if (
            record.result is CriterionResult.INDETERMINATE
            and late
            and EvaluationReasonCode.LATE_OBSERVATION not in record.reason_codes
        ):
            raise ContractReferenceError("late observation requires its reason code")

    @classmethod
    def _validate_record(
        cls,
        connection: sqlite3.Connection,
        record: ApplicationRecord,
    ) -> None:
        if isinstance(record, LessonApplication):
            cls._validate_application(connection, record)
        elif isinstance(record, OutcomeObservation):
            cls._validate_observation(connection, record)
        elif isinstance(record, CriterionEvaluation):
            cls._validate_evaluation(connection, record)

    def save(self, record: ApplicationStoredRecord) -> ApplicationStoredRecord:
        if not isinstance(record, tuple(APPLICATION_RECORD_MODELS.values())):
            return super().save(record)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if isinstance(record, LessonApplication):
                    existing = self._idempotent_replay(connection, record)
                    if existing is not None:
                        connection.execute("COMMIT")
                        return existing
                missing = self._missing_references(connection, record)
                if missing:
                    raise MissingReferenceError(
                        f"Cannot save {record.id}; missing referenced records: {', '.join(missing)}"
                    )
                self._validate_record(connection, record)
                self._insert_record(connection, record)
                if isinstance(record, LessonApplication):
                    connection.execute(
                        """
                        INSERT INTO application_idempotency_keys (
                            record_type, idempotency_key, payload_hash, record_id
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
                    raise IdempotencyConflictError(
                        "application id or idempotency key already exists"
                    ) from error
                raise DuplicateRecordError(f"Record already exists: {record.id}") from error
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def list(self, record_type: str | None = None) -> list[ApplicationStoredRecord]:
        query = "SELECT id FROM records"
        parameters: tuple[str, ...] = ()
        if record_type is not None:
            if record_type not in APPLICATION_STORAGE_RECORD_MODELS:
                raise ValueError(f"Unknown record type: {record_type}")
            query += " WHERE type = ?"
            parameters = (record_type,)
        query += " ORDER BY created_at, id"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
            return [self._load_record(connection, row["id"]) for row in rows]

    def delete(self, record_id: str, *, force: bool = False) -> None:
        record = self.get(record_id)
        if isinstance(record, tuple(APPLICATION_RECORD_MODELS.values())):
            raise ReferencedRecordError(
                "application history is append-only; create a superseding record"
            )
        super().delete(record_id, force=force)
