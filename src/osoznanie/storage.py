"""SQLite persistence with provenance and atomic memory-head validation."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .consolidation import AmbiguousMemoryHistoryError, ConsolidationResult
from .memory import MemoryObject
from .memory_repository import (
    InvalidMemoryProgressionError,
    MemoryHead,
    UnsafeMemoryWriteError,
    VersionConflictError,
)
from .models import RECORD_MODELS, Record


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


StoredRecord = Record | MemoryObject
STORAGE_RECORD_MODELS = {**RECORD_MODELS, "memory": MemoryObject}


class SQLiteExperienceStore:
    """Store protocol records and atomically manage logical memory heads.

    Generic protocol records are stored as validated JSON documents. Memory versions
    use relational index tables so SQLite can enforce one version number per logical
    memory and one explicit head per version chain.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._memory_connection: sqlite3.Connection | None = None
        if self.path == ":memory:":
            self._memory_connection = self._new_connection()
        self.initialize()

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=5.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        if self.path != ":memory:":
            connection.execute("PRAGMA journal_mode = WAL")
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
                CREATE TABLE IF NOT EXISTS memory_versions (
                    memory_id TEXT PRIMARY KEY REFERENCES records(id),
                    memory_key TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version >= 1),
                    UNIQUE(memory_key, version)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memory_versions_key
                ON memory_versions(memory_key)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_heads (
                    memory_key TEXT PRIMARY KEY,
                    current_id TEXT NOT NULL REFERENCES records(id),
                    current_version INTEGER NOT NULL CHECK(current_version >= 1),
                    updated_at TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def _record_exists(connection: sqlite3.Connection, record_id: str) -> bool:
        row = connection.execute(
            "SELECT 1 FROM records WHERE id = ?",
            (record_id,),
        ).fetchone()
        return row is not None

    def exists(self, record_id: str) -> bool:
        with self._connect() as connection:
            return self._record_exists(connection, record_id)

    @staticmethod
    def _missing_references(
        connection: sqlite3.Connection,
        record: StoredRecord,
    ) -> list[str]:
        return [
            reference
            for reference in record.reference_ids()
            if not SQLiteExperienceStore._record_exists(connection, reference)
        ]

    @staticmethod
    def _insert_record(
        connection: sqlite3.Connection,
        record: StoredRecord,
    ) -> None:
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

    @staticmethod
    def _load_record(
        connection: sqlite3.Connection,
        record_id: str,
    ) -> StoredRecord:
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

    def save(self, record: StoredRecord) -> StoredRecord:
        """Save a generic record or an initial version-1 memory.

        Later memory versions must use :meth:`commit_memory` so a caller cannot
        bypass compare-and-swap head validation.
        """
        if isinstance(record, MemoryObject):
            if record.version != 1 or record.supersedes:
                raise UnsafeMemoryWriteError(
                    "memory versions after 1 must be written with commit_memory()"
                )
            return self._commit_memory_object(
                record,
                previous_memory_id=None,
                expected_head_id=None,
                expected_version=None,
            )

        with self._connect() as connection:
            missing = self._missing_references(connection, record)
            if missing:
                raise MissingReferenceError(
                    f"Cannot save {record.id}; missing referenced records: "
                    f"{', '.join(missing)}"
                )
            try:
                self._insert_record(connection, record)
            except sqlite3.IntegrityError as error:
                raise DuplicateRecordError(
                    f"Record already exists: {record.id}"
                ) from error
        return record

    def get(self, record_id: str) -> StoredRecord:
        with self._connect() as connection:
            return self._load_record(connection, record_id)

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
            return [self._load_record(connection, row["id"]) for row in rows]

    def get_memory_head(self, memory_key: str) -> MemoryHead | None:
        normalized_key = memory_key.strip()
        if not normalized_key:
            raise ValueError("memory_key must not be blank")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT memory_key, current_id, current_version, updated_at
                FROM memory_heads
                WHERE memory_key = ?
                """,
                (normalized_key,),
            ).fetchone()
        if row is None:
            return None
        return MemoryHead(
            memory_key=row["memory_key"],
            current_id=row["current_id"],
            current_version=row["current_version"],
            updated_at=row["updated_at"],
        )

    def commit_memory(
        self,
        result: ConsolidationResult,
        *,
        expected_head_id: str | None,
        expected_version: int | None = None,
    ) -> MemoryObject:
        """Atomically compare the current head and commit the next memory version."""
        return self._commit_memory_object(
            result.memory,
            previous_memory_id=result.previous_memory_id,
            expected_head_id=expected_head_id,
            expected_version=expected_version,
        )

    def _commit_memory_object(
        self,
        memory: MemoryObject,
        *,
        previous_memory_id: str | None,
        expected_head_id: str | None,
        expected_version: int | None,
    ) -> MemoryObject:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT current_id, current_version
                    FROM memory_heads
                    WHERE memory_key = ?
                    """,
                    (memory.memory_key,),
                ).fetchone()
                actual_head_id = None if row is None else row["current_id"]
                actual_version = None if row is None else row["current_version"]

                if actual_head_id == memory.id:
                    stored = self._load_record(connection, memory.id)
                    if stored != memory or actual_version != memory.version:
                        raise AmbiguousMemoryHistoryError(
                            f"head {memory.id} does not match its stored payload/version"
                        )
                    connection.execute("COMMIT")
                    return memory

                if (
                    expected_head_id != actual_head_id
                    or (
                        expected_version is not None
                        and expected_version != actual_version
                    )
                ):
                    raise VersionConflictError(
                        memory_key=memory.memory_key,
                        expected_head_id=expected_head_id,
                        actual_head_id=actual_head_id,
                        expected_version=expected_version,
                        actual_version=actual_version,
                    )

                self._validate_memory_progression(
                    connection,
                    memory,
                    previous_memory_id=previous_memory_id,
                    actual_head_id=actual_head_id,
                    actual_version=actual_version,
                )

                missing = self._missing_references(connection, memory)
                if missing:
                    raise MissingReferenceError(
                        f"Cannot save {memory.id}; missing referenced records: "
                        f"{', '.join(missing)}"
                    )

                try:
                    self._insert_record(connection, memory)
                    connection.execute(
                        """
                        INSERT INTO memory_versions (memory_id, memory_key, version)
                        VALUES (?, ?, ?)
                        """,
                        (memory.id, memory.memory_key, memory.version),
                    )
                except sqlite3.IntegrityError as error:
                    raise AmbiguousMemoryHistoryError(
                        "memory id or (memory_key, version) already belongs to "
                        "another committed history node"
                    ) from error

                if actual_head_id is None:
                    connection.execute(
                        """
                        INSERT INTO memory_heads (
                            memory_key, current_id, current_version, updated_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            memory.memory_key,
                            memory.id,
                            memory.version,
                            memory.updated_at.isoformat(),
                        ),
                    )
                else:
                    cursor = connection.execute(
                        """
                        UPDATE memory_heads
                        SET current_id = ?, current_version = ?, updated_at = ?
                        WHERE memory_key = ?
                          AND current_id = ?
                          AND current_version = ?
                        """,
                        (
                            memory.id,
                            memory.version,
                            memory.updated_at.isoformat(),
                            memory.memory_key,
                            actual_head_id,
                            actual_version,
                        ),
                    )
                    if cursor.rowcount != 1:
                        latest = connection.execute(
                            """
                            SELECT current_id, current_version
                            FROM memory_heads
                            WHERE memory_key = ?
                            """,
                            (memory.memory_key,),
                        ).fetchone()
                        raise VersionConflictError(
                            memory_key=memory.memory_key,
                            expected_head_id=actual_head_id,
                            actual_head_id=(
                                None if latest is None else latest["current_id"]
                            ),
                            expected_version=actual_version,
                            actual_version=(
                                None if latest is None else latest["current_version"]
                            ),
                        )

                connection.execute("COMMIT")
                return memory
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def _validate_memory_progression(
        self,
        connection: sqlite3.Connection,
        memory: MemoryObject,
        *,
        previous_memory_id: str | None,
        actual_head_id: str | None,
        actual_version: int | None,
    ) -> None:
        if actual_head_id is None:
            if (
                memory.version != 1
                or memory.supersedes
                or previous_memory_id is not None
            ):
                raise InvalidMemoryProgressionError(
                    "a new memory chain must begin at version 1 without supersedes"
                )
            return

        assert actual_version is not None
        if previous_memory_id != actual_head_id:
            raise InvalidMemoryProgressionError(
                "consolidation result previous_memory_id does not match stored head"
            )
        if memory.version != actual_version + 1:
            raise InvalidMemoryProgressionError(
                f"memory version must advance from {actual_version} to "
                f"{actual_version + 1}, got {memory.version}"
            )
        if memory.supersedes != [actual_head_id]:
            raise InvalidMemoryProgressionError(
                "next memory version must supersede exactly the current head"
            )

        previous = self._load_record(connection, actual_head_id)
        if not isinstance(previous, MemoryObject):
            raise AmbiguousMemoryHistoryError(
                f"memory head {actual_head_id} is not a MemoryObject"
            )
        if previous.memory_key != memory.memory_key:
            raise AmbiguousMemoryHistoryError(
                "memory head key does not match its memory_heads entry"
            )
        if previous.memory_type is not memory.memory_type:
            raise InvalidMemoryProgressionError(
                "memory_type cannot change across committed versions"
            )

    def delete(self, record_id: str, *, force: bool = False) -> None:
        with self._connect() as connection:
            if not self._record_exists(connection, record_id):
                raise RecordNotFoundError(record_id)

            memory_row = connection.execute(
                "SELECT memory_key, version FROM memory_versions WHERE memory_id = ?",
                (record_id,),
            ).fetchone()
            if memory_row is not None:
                raise ReferencedRecordError(
                    "committed memory history is immutable; create a revoke or "
                    "superseding version instead"
                )

            dependants = [
                record.id
                for record in self.list()
                if record.id != record_id and record_id in record.reference_ids()
            ]
            if dependants and not force:
                raise ReferencedRecordError(
                    f"Record {record_id} is referenced by: {', '.join(dependants)}"
                )
            connection.execute("DELETE FROM records WHERE id = ?", (record_id,))

    def explain(self, record_id: str) -> dict[str, object]:
        """Return an inspectable provenance view for one record."""
        record = self.get(record_id)
        references = [self.get(reference) for reference in record.reference_ids()]
        return {
            "record": record.model_dump(mode="json"),
            "references": [item.model_dump(mode="json") for item in references],
        }
