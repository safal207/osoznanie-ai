from benchmarks.audit_paths import build_audit_path_bundle
from benchmarks.models import StrategyName
from benchmarks.simulate import run_decision_simulation
from benchmarks.simulation_fixtures import build_decision_simulation_cases


def test_filter_projection_does_not_expose_filtered_ids() -> None:
    cases = build_decision_simulation_cases()
    try:
        report = run_decision_simulation(cases)
        bundle = build_audit_path_bundle(cases, report)
    finally:
        for case in cases:
            case.retrieval_case.store.close()

    scenario_id = cases[0].scenario.scenario_id
    key = (scenario_id, StrategyName.OSOZNANIE_RECALL)
    public = next(
        graph
        for graph in bundle.public_graphs
        if (graph.scenario_id, graph.strategy) == key
    )
    audit = next(
        item
        for item in bundle.audits
        if (item.scenario_id, item.strategy) == key
    )
    filtered_id = f"les_{scenario_id}_access_denied"

    assert filtered_id not in public.model_dump_json()
    assert filtered_id not in audit.model_dump_json()
    assert public.filter_summary is not None
    assert public.filter_summary.access_denied.value is None
    assert audit.filter_summary is not None
    assert audit.filter_summary.access_denied.value == 1
