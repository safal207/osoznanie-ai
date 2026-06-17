import json
from pathlib import Path

from benchmarks.audit_paths import (
    build_audit_path_bundle,
    render_public_mermaid,
    write_audit_path_bundle,
)
from benchmarks.models import StrategyName
from benchmarks.path_contracts import DecisionPathStatus
from benchmarks.simulate import run_decision_simulation
from benchmarks.simulation_fixtures import build_decision_simulation_cases


def _close_cases(cases) -> None:
    for case in cases:
        case.retrieval_case.store.close()


def _bundle():
    cases = build_decision_simulation_cases()
    try:
        report = run_decision_simulation(cases)
        return build_audit_path_bundle(cases, report)
    finally:
        _close_cases(cases)


def test_bundle_has_public_and_restricted_artifact_per_trial() -> None:
    bundle = _bundle()

    assert len(bundle.public_graphs) == 9
    assert len(bundle.audits) == 9
    assert bundle.manifest.graph_count == 9
    assert len(bundle.manifest.entries) == 9
    assert all(entry.audit_json_file.endswith(".audit.json") for entry in bundle.manifest.entries)


def test_public_graph_has_closed_statuses_and_reason_codes() -> None:
    bundle = _bundle()

    for graph in bundle.public_graphs:
        assert graph.status is not None
        assert graph.reason_code is not None
        if graph.strategy is StrategyName.OSOZNANIE_RECALL:
            assert graph.status is DecisionPathStatus.SAFE_DECISION
        else:
            assert graph.status is DecisionPathStatus.REPEATED_ERROR


def test_public_payload_excludes_restricted_fields() -> None:
    bundle = _bundle()
    forbidden = {
        "canonical_score",
        "score_breakdown",
        "reason_codes",
        "provenance_refs",
        "error_signature",
        "safe_action_id",
        "repeated_error_action_id",
        "statement",
    }

    for graph in bundle.public_graphs:
        payload = graph.model_dump_json()
        for key in forbidden:
            assert f'"{key}"' not in payload


def test_restricted_payload_contains_typed_recall_metadata_only_when_available() -> None:
    bundle = _bundle()

    for audit in bundle.audits:
        if audit.strategy is StrategyName.NO_MEMORY:
            assert audit.ranking_policy is None
            assert audit.returned_lessons == []
        elif audit.strategy is StrategyName.NAIVE_KEYWORD:
            assert audit.ranking_policy is not None
            assert audit.returned_lessons
            assert all(item.score_breakdown is None for item in audit.returned_lessons)
            assert all(item.reason_codes == [] for item in audit.returned_lessons)
            assert all(item.provenance_refs == [] for item in audit.returned_lessons)
        else:
            assert audit.ranking_policy is not None
            assert audit.ranking_policy.id == "recall-ranking-v0.2"
            assert len(audit.returned_lessons) == 1
            lesson = audit.returned_lessons[0]
            assert lesson.score_breakdown is not None
            assert lesson.reason_codes
            assert lesson.provenance_refs
            payload = json.loads(audit.model_dump_json())
            assert isinstance(payload["returned_lessons"][0]["canonical_score"], str)
            assert payload["ranking_policy"]["score_bucket_width"] == "0.000001"


def test_artifacts_are_byte_reproducible_and_use_split_names(tmp_path: Path) -> None:
    first = _bundle()
    second = _bundle()

    first_manifest, first_paths = write_audit_path_bundle(first, tmp_path / "first")
    second_manifest, second_paths = write_audit_path_bundle(second, tmp_path / "second")

    assert first_manifest.read_bytes() == second_manifest.read_bytes()
    first_bytes = {
        path.relative_to(tmp_path / "first"): path.read_bytes()
        for path in first_paths
    }
    second_bytes = {
        path.relative_to(tmp_path / "second"): path.read_bytes()
        for path in second_paths
    }
    assert first_bytes == second_bytes
    assert len(first_paths) == 27
    assert sum(path.name.endswith(".public.json") for path in first_paths) == 9
    assert sum(path.name.endswith(".public.mmd") for path in first_paths) == 9
    assert sum(path.name.endswith(".audit.json") for path in first_paths) == 9

    mermaid = render_public_mermaid(first.public_graphs[0])
    assert mermaid.startswith("flowchart LR\n")
    assert "reason_code:" in mermaid
    assert "evaluated_as" in mermaid
