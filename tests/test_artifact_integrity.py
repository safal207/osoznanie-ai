from pathlib import Path

import pytest

import benchmarks.audit_paths as audit_paths
from benchmarks.artifact_integrity import (
    CURRENT_POINTER_FILE,
    ArtifactFileDigest,
    ArtifactIntegrityError,
    ArtifactIntegrityIndex,
    verify_integrity_index,
)
from benchmarks.audit_paths import (
    MANIFEST_FILE_NAME,
    build_audit_path_bundle,
    verify_published_audit_path_set,
    write_audit_path_bundle,
)
from benchmarks.simulate import run_decision_simulation
from benchmarks.simulation_fixtures import build_decision_simulation_cases


def _build_bundle():
    cases = build_decision_simulation_cases()
    try:
        report = run_decision_simulation(cases)
        return build_audit_path_bundle(cases, report)
    finally:
        for case in cases:
            case.retrieval_case.store.close()


def test_atomic_publish_is_verifiable_and_reproducible(tmp_path: Path) -> None:
    bundle = _build_bundle()
    first_manifest, first_files = write_audit_path_bundle(bundle, tmp_path / "first")
    second_manifest, second_files = write_audit_path_bundle(bundle, tmp_path / "second")

    first = verify_published_audit_path_set(tmp_path / "first")
    second = verify_published_audit_path_set(tmp_path / "second")

    assert first.set_id == second.set_id
    assert first_manifest.read_bytes() == second_manifest.read_bytes()
    assert len(first_files) == len(second_files) == 27
    assert (tmp_path / "first" / CURRENT_POINTER_FILE).is_file()


def test_one_changed_byte_breaks_verification(tmp_path: Path) -> None:
    bundle = _build_bundle()
    _, files = write_audit_path_bundle(bundle, tmp_path / "published")
    target = files[0]
    target.write_bytes(target.read_bytes() + b"x")

    with pytest.raises(ArtifactIntegrityError, match="mismatch"):
        verify_published_audit_path_set(tmp_path / "published")


def test_missing_and_extra_files_break_verification(tmp_path: Path) -> None:
    bundle = _build_bundle()
    _, files = write_audit_path_bundle(bundle, tmp_path / "missing")
    files[0].unlink()
    with pytest.raises(ArtifactIntegrityError, match="missing"):
        verify_published_audit_path_set(tmp_path / "missing")

    _, files = write_audit_path_bundle(bundle, tmp_path / "extra")
    (files[0].parent / "unregistered.txt").write_text("extra", encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError, match="unregistered"):
        verify_published_audit_path_set(tmp_path / "extra")


def test_manifest_cannot_hash_itself(tmp_path: Path) -> None:
    manifest = tmp_path / MANIFEST_FILE_NAME
    manifest.write_text("{}", encoding="utf-8")
    integrity = ArtifactIntegrityIndex(
        files=[
            ArtifactFileDigest(
                path=MANIFEST_FILE_NAME,
                sha256="0" * 64,
                size_bytes=2,
            )
        ]
    )

    with pytest.raises(ArtifactIntegrityError, match="must not hash itself"):
        verify_integrity_index(tmp_path, MANIFEST_FILE_NAME, integrity)


def test_failed_staging_preserves_previous_current_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _build_bundle()
    publication_root = tmp_path / "published"
    write_audit_path_bundle(bundle, publication_root)
    pointer_path = publication_root / CURRENT_POINTER_FILE
    previous_pointer = pointer_path.read_bytes()
    original_write = audit_paths.write_text_durable
    calls = 0

    def fail_during_staging(path: Path, value: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated interrupted staging")
        original_write(path, value)

    monkeypatch.setattr(audit_paths, "write_text_durable", fail_during_staging)
    with pytest.raises(OSError, match="interrupted staging"):
        write_audit_path_bundle(bundle, publication_root)

    assert pointer_path.read_bytes() == previous_pointer
    verify_published_audit_path_set(publication_root)
    assert not list((publication_root / "sets").glob(".staging-*"))
