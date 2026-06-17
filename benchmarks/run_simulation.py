"""Command-line entry point for deterministic decision simulation."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from .audit_paths import build_audit_path_bundle, write_audit_path_bundle
from .simulate import run_decision_simulation, write_decision_report
from .simulation_fixtures import build_decision_simulation_cases


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the deterministic Osoznanie decision-policy simulation."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmark-results"),
        help="Directory for generated benchmark files.",
    )
    args = parser.parse_args(argv)

    cases = build_decision_simulation_cases()
    try:
        report = run_decision_simulation(cases)
        json_path, markdown_path = write_decision_report(report, args.output)
        bundle = build_audit_path_bundle(cases, report)
        manifest_path, graph_paths = write_audit_path_bundle(
            bundle,
            args.output / "decision-paths",
        )
    finally:
        for case in cases:
            case.retrieval_case.store.close()

    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")
    print(f"Decision-path manifest: {manifest_path}")
    print(f"Decision-path files: {len(graph_paths)}")
    for item in report.aggregates:
        print(
            f"{item.strategy.value}: correct={item.correct_decision_rate:.3f}, "
            f"repeated_error={item.repeated_error_rate:.3f}, "
            f"lesson_application={item.lesson_application_rate:.3f}, "
            f"abstention={item.abstention_rate:.3f}, "
            f"coverage={item.policy_coverage:.3f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
