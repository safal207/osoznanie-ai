from pathlib import Path

from benchmarks.decision_paths import (
    DecisionPathNodeKind,
    DecisionPathStatus,
    build_decision_path_bundle,
    render_mermaid,
    write_decision_path_bundle,
)
from benchmarks.models import StrategyName
from benchmarks.simulate import run_decision_simulation
from benchmarks.simulation_fixtures import build_decision_simulation_cases


def _close_cases(cases) -> None:
    for case in cases:
        case.retrieval_case.store.close()


def test_bundle_contains_one_graph_per_trial() -> None:
    cases = build_decision_simulation_cases()
    try:
        report = run_decision_simulation(cases)
        bundle = build_decision_path_bundle(cases, report)
    finally:
        _close_cases(cases)

    assert len(report.trial_results) == 9
    assert len(bundle.graphs) == 9
    assert bundle.manifest.graph_count == 9
    assert len(bundle.manifest.entries) == 9


def test_graph_statuses_match_decision_results() -> None:
    cases = build_decision_simulation_cases()
    try:
        report = run_decision_simulation(cases)
        bundle = build_decision_path_bundle(cases, report)
    finally:
        _close_cases(cases)

    statuses = {
        (graph.scenario_id, graph.strategy): graph.status
        for graph in bundle.graphs
    }
    for result in report.trial_results:
        status = statuses[(result.scenario_id, result.strategy)]
        if result.strategy is StrategyName.OSOZNANIE_RECALL:
            assert status is DecisionPathStatus.SAFE_DECISION
        else:
            assert status is DecisionPathStatus.REPEATED_ERROR


def test_graph_redacts_scores_and_lesson_statements() -> None:
    cases = build_decision_simulation_cases()
    try:
        report = run_decision_simulation(cases)
        bundle = build_decision_path_bundle(cases, report)
    finally:
        _close_cases(cases)

    forbidden_keys = {
        "score",
        "retrieval_score",
        "error_signature",
        "safe_action_id",
        "repeated_error_action_id",
        "chain_of_thought",
        "reasoning",
        "statement",
    }
    for graph in bundle.graphs:
        for node in graph.nodes:
            assert forbidden_keys.isdisjoint(node.metadata)
            if node.kind is DecisionPathNodeKind.LESSON:
                assert node.label.startswith("Lesson #")
                assert "Use the supported" not in node.label
                assert "Quality assurance" not in node.label


def test_outcome_is_the_only_evaluator_only_node() -> None:
    cases = build_decision_simulation_cases()
    try:
        report = run_decision_simulation(cases)
        bundle = build_decision_path_bundle(cases, report)
    finally:
        _close_cases(cases)

    for graph in bundle.graphs:
        evaluator_nodes = [node for node in graph.nodes if node.evaluator_only]
        assert len(evaluator_nodes) == 1
        assert evaluator_nodes[0].kind is DecisionPathNodeKind.OUTCOME


def test_no_memory_graph_skips_lesson_nodes() -> None:
    cases = build_decision_simulation_cases()
    try:
        report = run_decision_simulation(cases)
        bundle = build_decision_path_bundle(cases, report)
    finally:
        _close_cases(cases)

    no_memory_graphs = [
        graph
        for graph in bundle.graphs
        if graph.strategy is StrategyName.NO_MEMORY
    ]
    assert len(no_memory_graphs) == 3
    for graph in no_memory_graphs:
        assert all(
            node.kind is not DecisionPathNodeKind.LESSON
            for node in graph.nodes
        )
        assert any(edge.relation == "returns_empty" for edge in graph.edges)


def test_mermaid_and_json_are_byte_reproducible(tmp_path: Path) -> None:
    first_cases = build_decision_simulation_cases()
    second_cases = build_decision_simulation_cases()
    try:
        first_report = run_decision_simulation(first_cases)
        second_report = run_decision_simulation(second_cases)
        first_bundle = build_decision_path_bundle(first_cases, first_report)
        second_bundle = build_decision_path_bundle(second_cases, second_report)
        first_manifest, first_paths = write_decision_path_bundle(
            first_bundle,
            tmp_path / "first",
        )
        second_manifest, second_paths = write_decision_path_bundle(
            second_bundle,
            tmp_path / "second",
        )
    finally:
        _close_cases(first_cases)
        _close_cases(second_cases)

    assert first_manifest.read_bytes() == second_manifest.read_bytes()
    first_relative = {
        path.relative_to(tmp_path / "first"): path.read_bytes()
        for path in first_paths
    }
    second_relative = {
        path.relative_to(tmp_path / "second"): path.read_bytes()
        for path in second_paths
    }
    assert first_relative == second_relative

    sample = first_bundle.graphs[0]
    mermaid = render_mermaid(sample)
    assert mermaid.startswith("flowchart LR\n")
    assert "requests_memory" in mermaid
    assert "evaluated_as" in mermaid
