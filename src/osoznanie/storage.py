"""Minimal SQLite persistence with provenance validation."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .memory import MemoryObject
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
    """Store protocol records as validated JSON documents.

    SQLite is intentionally used for the first implementation so that the
    protocol can be tested without external infrastructure. Provenance links
    are validated before writes even though records are stored generically.
    """

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
            connection.commit()

    def exists(self, record_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM records WHERE id = ?",
                (record_id,),
            ).fetchone()
        return row is not None

    def save(self, record: StoredRecord) -> StoredRecord:
        missing = [reference for reference in record.reference_ids() if not self.exists(reference)]
        if missing:
            raise MissingReferenceError(
                f"Cannot save {record.id}; missing referenced records: {', '.join(missing)}"
            )

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
                connection.commit()
        except sqlite3.IntegrityError as error:
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
