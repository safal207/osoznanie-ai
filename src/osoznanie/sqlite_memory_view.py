"""SQLite read adapter for bitemporal memory-state projection."""

from __future__ import annotations

from .memory import MemoryObject
from .memory_view import CommittedMemoryVersion
from .storage import SQLiteExperienceStore, StorageError


class SQLiteMemoryViewStore:
    """Expose committed memory versions without coupling projection to writes.

    In protocol v0.1, ``records.updated_at`` is populated by SQLite at insertion
    time and is treated as the knowledge-time timestamp. The column name is legacy;
    committed memory records are immutable and are never updated in place.
    """

    def __init__(self, store: SQLiteExperienceStore) -> None:
        self.store = store

    def list_committed_memory_versions(self) -> list[CommittedMemoryVersion]:
        with self.store._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    mv.memory_id,
                    mv.memory_key,
                    mv.version,
                    r.updated_at AS committed_at
                FROM memory_versions AS mv
                JOIN records AS r ON r.id = mv.memory_id
                ORDER BY mv.memory_key, mv.version, mv.memory_id
                """
            ).fetchall()

            committed: list[CommittedMemoryVersion] = []
            for row in rows:
                record = self.store._load_record(connection, row["memory_id"])
                if not isinstance(record, MemoryObject):
                    raise StorageError(
                        f"memory_versions row {row['memory_id']} is not a MemoryObject"
                    )
                if (
                    record.memory_key != row["memory_key"]
                    or record.version != row["version"]
                ):
                    raise StorageError(
                        "memory_versions index does not match immutable memory payload: "
                        f"{record.id}"
                    )
                committed.append(
                    CommittedMemoryVersion(
                        memory=record,
                        committed_at=row["committed_at"],
                    )
                )

        return committed
