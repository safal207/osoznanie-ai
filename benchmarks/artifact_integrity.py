"""Integrity and atomic publication primitives for generated artifact sets."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

HASH_ALGORITHM = "sha256"
CURRENT_POINTER_FILE = "current.json"
SETS_DIRECTORY = "sets"


class ArtifactIntegrityError(RuntimeError):
    """Raised when an artifact set is incomplete, modified, or unsafe."""


class ArtifactFileDigest(BaseModel):
    """Digest of one authoritative artifact file, relative to its set root."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or value != path.as_posix():
            raise ValueError("artifact path must be a canonical safe relative path")
        return value


class ArtifactIntegrityIndex(BaseModel):
    """Deterministically ordered hashes for files covered by a manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    algorithm: Literal["sha256"] = HASH_ALGORITHM
    files: list[ArtifactFileDigest]

    @model_validator(mode="after")
    def validate_order_and_uniqueness(self) -> ArtifactIntegrityIndex:
        paths = [item.path for item in self.files]
        if paths != sorted(paths):
            raise ValueError("artifact digests must be sorted by path")
        if len(paths) != len(set(paths)):
            raise ValueError("artifact digest paths must be unique")
        return self


class ArtifactSetPointer(BaseModel):
    """Atomic commit marker pointing to one immutable versioned set."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    set_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_file: str
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("manifest_file")
    @classmethod
    def validate_manifest_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or value != path.as_posix():
            raise ValueError("manifest path must be a canonical safe relative path")
        return value

    @model_validator(mode="after")
    def validate_set_identity(self) -> ArtifactSetPointer:
        if self.set_id != self.manifest_sha256:
            raise ValueError("set ID must equal the manifest SHA-256")
        expected_prefix = f"{SETS_DIRECTORY}/{self.set_id}/"
        if not self.manifest_file.startswith(expected_prefix):
            raise ValueError("manifest path must be inside its versioned set")
        return self


class PublishedArtifactSet(BaseModel):
    """Resolved paths for the current published artifact set."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    set_id: str
    set_dir: Path
    manifest_path: Path
    pointer_path: Path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_bytes_durable(path: Path, data: bytes) -> None:
    """Write, flush, and fsync a file before it can be hashed or published."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def write_text_durable(path: Path, value: str) -> None:
    write_bytes_durable(path, value.encode("utf-8"))


def fsync_directory(path: Path) -> None:
    """Persist directory entry changes where the platform supports directory fsync."""

    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(descriptor)
        except OSError:
            return
    finally:
        os.close(descriptor)


def create_staging_directory(publication_root: Path) -> Path:
    sets_dir = publication_root / SETS_DIRECTORY
    sets_dir.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=".staging-", dir=sets_dir))


def build_integrity_index(
    set_root: Path,
    relative_paths: list[str],
) -> ArtifactIntegrityIndex:
    records: list[ArtifactFileDigest] = []
    for relative in sorted(relative_paths):
        safe_relative = ArtifactFileDigest(
            path=relative,
            sha256="0" * 64,
            size_bytes=0,
        ).path
        path = set_root / safe_relative
        if path.is_symlink() or not path.is_file():
            raise ArtifactIntegrityError(f"artifact file is missing or unsafe: {relative}")
        records.append(
            ArtifactFileDigest(
                path=safe_relative,
                sha256=sha256_file(path),
                size_bytes=path.stat().st_size,
            )
        )
    return ArtifactIntegrityIndex(files=records)


def verify_integrity_index(
    set_root: Path,
    manifest_name: str,
    integrity: ArtifactIntegrityIndex,
) -> None:
    """Verify hashes, sizes, missing files, extra files, and manifest self-exclusion."""

    expected = {item.path for item in integrity.files}
    if manifest_name in expected:
        raise ArtifactIntegrityError("manifest must not hash itself")

    actual: set[str] = set()
    for path in set_root.rglob("*"):
        if path.is_symlink():
            raise ArtifactIntegrityError(f"symbolic links are not allowed: {path}")
        if path.is_file():
            actual.add(path.relative_to(set_root).as_posix())

    allowed = expected | {manifest_name}
    missing = expected - actual
    extra = actual - allowed
    if manifest_name not in actual:
        raise ArtifactIntegrityError("manifest file is missing")
    if missing:
        raise ArtifactIntegrityError(f"registered artifact files are missing: {sorted(missing)}")
    if extra:
        raise ArtifactIntegrityError(f"unregistered artifact files are present: {sorted(extra)}")

    for item in integrity.files:
        path = set_root / item.path
        if path.stat().st_size != item.size_bytes:
            raise ArtifactIntegrityError(f"artifact size mismatch: {item.path}")
        if sha256_file(path) != item.sha256:
            raise ArtifactIntegrityError(f"artifact hash mismatch: {item.path}")


def _tree_fingerprints(root: Path) -> dict[str, tuple[int, str]]:
    fingerprints: dict[str, tuple[int, str]] = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ArtifactIntegrityError(f"symbolic links are not allowed: {path}")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            fingerprints[relative] = (path.stat().st_size, sha256_file(path))
    return fingerprints


def publish_staged_set(
    staging_dir: Path,
    publication_root: Path,
    manifest_name: str,
) -> PublishedArtifactSet:
    """Publish an immutable set, then atomically replace its current pointer."""

    manifest_path = staging_dir / manifest_name
    if not manifest_path.is_file():
        raise ArtifactIntegrityError("staged set has no manifest")

    manifest_sha256 = sha256_file(manifest_path)
    set_id = manifest_sha256
    sets_dir = publication_root / SETS_DIRECTORY
    final_dir = sets_dir / set_id

    if final_dir.exists():
        if _tree_fingerprints(final_dir) != _tree_fingerprints(staging_dir):
            raise ArtifactIntegrityError("existing set ID has different file content")
        shutil.rmtree(staging_dir)
    else:
        os.replace(staging_dir, final_dir)
        fsync_directory(sets_dir)

    pointer = ArtifactSetPointer(
        set_id=set_id,
        manifest_file=f"{SETS_DIRECTORY}/{set_id}/{manifest_name}",
        manifest_sha256=manifest_sha256,
    )
    pointer_path = publication_root / CURRENT_POINTER_FILE
    temporary_pointer = publication_root / f".{CURRENT_POINTER_FILE}.{uuid4().hex}.tmp"
    try:
        write_text_durable(temporary_pointer, pointer.model_dump_json(indent=2) + "\n")
        os.replace(temporary_pointer, pointer_path)
    except Exception:
        temporary_pointer.unlink(missing_ok=True)
        raise
    fsync_directory(publication_root)

    return PublishedArtifactSet(
        set_id=set_id,
        set_dir=final_dir,
        manifest_path=final_dir / manifest_name,
        pointer_path=pointer_path,
    )


def resolve_current_set(publication_root: Path) -> PublishedArtifactSet:
    pointer_path = publication_root / CURRENT_POINTER_FILE
    if not pointer_path.is_file():
        raise ArtifactIntegrityError("current artifact-set pointer is missing")
    pointer = ArtifactSetPointer.model_validate_json(pointer_path.read_text("utf-8"))
    manifest_path = publication_root / pointer.manifest_file
    if not manifest_path.is_file():
        raise ArtifactIntegrityError("current manifest is missing")
    if sha256_file(manifest_path) != pointer.manifest_sha256:
        raise ArtifactIntegrityError("current manifest hash does not match pointer")
    return PublishedArtifactSet(
        set_id=pointer.set_id,
        set_dir=manifest_path.parent,
        manifest_path=manifest_path,
        pointer_path=pointer_path,
    )
