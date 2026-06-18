"""SQLite adapters for trusted policy reads and restricted memory reads."""

from __future__ import annotations

from .access_control import AuthorizedScope
from .memory import MemoryObject, MemoryType
from .memory_view import CommittedMemoryVersion
from .storage import SQLiteExperienceStore, StorageError


class SQLiteAccessPolicyStore:
    """Root-capability adapter that exposes access-policy history only."""

    def __init__(self, store: SQLiteExperienceStore) -> None:
        self.store = store

    def list_committed_memory_versions(self) -> list[CommittedMemoryVersion]:
        with self.store._connect() as connection:
            rows = connection.execute(
                """
                SELECT mv.memory_id, mv.memory_key, mv.version,
                       r.updated_at AS committed_at
                FROM memory_versions AS mv
                JOIN records AS r ON r.id = mv.memory_id
                WHERE json_extract(r.payload, '$.memory_type') = ?
                ORDER BY mv.memory_key, mv.version, mv.memory_id
                """,
                (MemoryType.ACCESS_POLICY.value,),
            ).fetchall()
            return [self._committed(connection, row) for row in rows]

    def _committed(self, connection, row) -> CommittedMemoryVersion:
        record = self.store._load_record(connection, row["memory_id"])
        if not isinstance(record, MemoryObject):
            raise StorageError(
                f"policy memory_versions row {row['memory_id']} is not a MemoryObject"
            )
        if record.memory_type is not MemoryType.ACCESS_POLICY:
            raise StorageError(f"trusted policy index returned non-policy {record.id}")
        return CommittedMemoryVersion(
            memory=record,
            committed_at=row["committed_at"],
        )


class SQLiteAuthorizedMemoryStore:
    """Load only rows admitted by requested selectors and projected policy rules."""

    def __init__(self, store: SQLiteExperienceStore) -> None:
        self.store = store
        self.last_loaded_memory_ids: list[str] = []

    def list_authorized_memory_versions(
        self,
        scope: AuthorizedScope,
    ) -> list[CommittedMemoryVersion]:
        self.last_loaded_memory_ids = []
        clauses = ["json_extract(r.payload, '$.memory_type') != ?"]
        parameters: list[str] = [MemoryType.ACCESS_POLICY.value]

        requested_parts: list[str] = []
        if scope.requested_memory_keys:
            placeholders = ",".join("?" for _ in scope.requested_memory_keys)
            requested_parts.append(f"mv.memory_key IN ({placeholders})")
            parameters.extend(scope.requested_memory_keys)
        for prefix in scope.requested_key_prefixes:
            requested_parts.append("mv.memory_key LIKE ?")
            parameters.append(f"{prefix}%")
        if scope.requested_memory_types:
            placeholders = ",".join("?" for _ in scope.requested_memory_types)
            requested_parts.append(
                f"json_extract(r.payload, '$.memory_type') IN ({placeholders})"
            )
            parameters.extend(item.value for item in scope.requested_memory_types)
        if requested_parts:
            clauses.append("(" + " OR ".join(requested_parts) + ")")

        query = f"""
            SELECT mv.memory_id, mv.memory_key, mv.version,
                   json_extract(r.payload, '$.memory_type') AS memory_type,
                   r.updated_at AS committed_at
            FROM memory_versions AS mv
            JOIN records AS r ON r.id = mv.memory_id
            WHERE {' AND '.join(clauses)}
            ORDER BY mv.memory_key, mv.version, mv.memory_id
        """

        with self.store._connect() as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()
            committed: list[CommittedMemoryVersion] = []
            for row in rows:
                memory_type = MemoryType(row["memory_type"])
                if not scope.allows(row["memory_key"], memory_type):
                    continue
                # Authorization is evaluated from indexed metadata before payload
                # deserialization. Denied payloads never cross the adapter boundary.
                record = self.store._load_record(connection, row["memory_id"])
                if not isinstance(record, MemoryObject):
                    raise StorageError(
                        f"memory_versions row {row['memory_id']} is not a MemoryObject"
                    )
                if (
                    record.memory_key != row["memory_key"]
                    or record.version != row["version"]
                    or record.memory_type is not memory_type
                ):
                    raise StorageError(
                        "memory index does not match immutable payload: "
                        f"{record.id}"
                    )
                self.last_loaded_memory_ids.append(record.id)
                committed.append(
                    CommittedMemoryVersion(
                        memory=record,
                        committed_at=row["committed_at"],
                    )
                )
        return committed
