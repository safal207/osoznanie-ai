"""Command-line entry point for the retrieval benchmark."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from .evaluate import run_benchmark, write_report
from .fixtures import build_benchmark_cases


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the deterministic Osoznanie retrieval quality benchmark."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmark-results"),
        help="Directory for JSON and Markdown reports.",
    )
    args = parser.parse_args(argv)

    cases = build_benchmark_cases()
    try:
        report = run_benchmark(cases)
        json_path, markdown_path = write_report(report, args.output)
    finally:
        for case in cases:
            case.store.close()

    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")
    for item in report.aggregates:
        print(
            f"{item.strategy.value}: hit@1={item.hit_rate_at_1:.3f}, "
            f"hit@3={item.hit_rate_at_3:.3f}, "
            f"mrr={item.mean_reciprocal_rank:.3f}, "
            f"fdr={item.mean_false_discovery_rate:.3f}, "
            f"dsr={item.mean_decoy_selection_rate:.3f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
