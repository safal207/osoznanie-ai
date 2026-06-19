"""Measure repeated-defect prevention from remembered QA rules in Chromium."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import mean

from osoznanie.memory import MemoryObject, MemoryType, resolve_active_memory
from osoznanie.playwright_runner import (
    PlaywrightBrowserCheckRunner,
    PlaywrightCheckInput,
)

from .playwright_pages import benchmark_checkout_server


@dataclass(frozen=True)
class Scenario:
    name: str
    buggy: bool


@dataclass(frozen=True)
class StrategyMetrics:
    strategy: str
    checks_executed: int
    missed_defects: int
    false_blocks: int
    mean_browser_latency_ms: float


@dataclass(frozen=True)
class PlaywrightMemoryReport:
    generated_at: str
    scenarios: int
    repeated_defects: int
    healthy_scenarios: int
    baseline: StrategyMetrics
    memory_guided: StrategyMetrics
    prevented_repeat_defects: int
    repeated_defect_prevention_rate: float
    memory_false_block_rate: float


def scenarios() -> list[Scenario]:
    return [
        Scenario("checkout-regression-a", True),
        Scenario("checkout-healthy-a", False),
        Scenario("checkout-regression-b", True),
        Scenario("checkout-healthy-b", False),
        Scenario("checkout-regression-c", True),
        Scenario("checkout-healthy-c", False),
    ]


def remembered_rule(at: datetime) -> MemoryObject:
    return MemoryObject(
        id="mem_benchmark_checkout_rule",
        memory_key="qa.rules.checkout.browser",
        memory_type=MemoryType.BEHAVIORAL_RULE,
        content={
            "statement": (
                "Run the checkout click-path browser check before release approval."
            ),
            "action_selector": "#checkout",
            "success_selector": "#confirmation",
        },
        source_event_ids=[],
        confidence=1.0,
        importance=1.0,
        valid_from=at - timedelta(days=1),
        created_at=at - timedelta(days=1),
        updated_at=at - timedelta(days=1),
    )


def run_benchmark() -> PlaywrightMemoryReport:
    now = datetime.now(UTC)
    cases = scenarios()
    defects = sum(case.buggy for case in cases)
    healthy = len(cases) - defects

    baseline_missed = defects
    baseline = StrategyMetrics(
        strategy="no_memory_no_browser_check",
        checks_executed=0,
        missed_defects=baseline_missed,
        false_blocks=0,
        mean_browser_latency_ms=0.0,
    )

    rule = remembered_rule(now)
    governing = resolve_active_memory(
        [rule],
        memory_key=rule.memory_key,
        at=now,
    )
    if governing is None:
        raise RuntimeError("active benchmark rule was not resolved")

    runner = PlaywrightBrowserCheckRunner()
    memory_missed = 0
    false_blocks = 0
    durations: list[int] = []
    with benchmark_checkout_server() as base_url:
        for index, case in enumerate(cases):
            page_kind = "buggy" if case.buggy else "healthy"
            evidence = runner.run(
                PlaywrightCheckInput(
                    release_id=f"benchmark-{index}",
                    target_url=f"{base_url}/{page_kind}-{index}",
                    action_selector=str(governing.content["action_selector"]),
                    success_selector=str(governing.content["success_selector"]),
                    changed_components=["checkout-button"],
                    timeout_ms=1_500,
                )
            )
            blocked = not evidence.passed
            durations.append(evidence.duration_ms)
            if case.buggy and not blocked:
                memory_missed += 1
            if not case.buggy and blocked:
                false_blocks += 1

    memory_guided = StrategyMetrics(
        strategy="active_memory_rule_plus_playwright",
        checks_executed=len(cases),
        missed_defects=memory_missed,
        false_blocks=false_blocks,
        mean_browser_latency_ms=round(mean(durations), 2),
    )
    prevented = baseline.missed_defects - memory_guided.missed_defects
    return PlaywrightMemoryReport(
        generated_at=now.isoformat(),
        scenarios=len(cases),
        repeated_defects=defects,
        healthy_scenarios=healthy,
        baseline=baseline,
        memory_guided=memory_guided,
        prevented_repeat_defects=prevented,
        repeated_defect_prevention_rate=(prevented / defects if defects else 0.0),
        memory_false_block_rate=(false_blocks / healthy if healthy else 0.0),
    )


def write_report(report: PlaywrightMemoryReport, output: Path) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "playwright-memory-report.json"
    markdown_path = output / "playwright-memory-report.md"
    json_path.write_text(
        json.dumps(asdict(report), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def _markdown(report: PlaywrightMemoryReport) -> str:
    return f"""# Playwright memory-effect benchmark

| Metric | Baseline | Memory-guided |
|---|---:|---:|
| Browser checks | {report.baseline.checks_executed} | {report.memory_guided.checks_executed} |
| Missed repeated defects | {report.baseline.missed_defects} | {report.memory_guided.missed_defects} |
| False release blocks | {report.baseline.false_blocks} | {report.memory_guided.false_blocks} |
| Mean browser latency, ms | {report.baseline.mean_browser_latency_ms:.2f} | {report.memory_guided.mean_browser_latency_ms:.2f} |

- Scenarios: {report.scenarios}
- Repeated defects: {report.repeated_defects}
- Prevented repeat defects: {report.prevented_repeat_defects}
- Repeated-defect prevention rate: {report.repeated_defect_prevention_rate:.3f}
- Memory false-block rate: {report.memory_false_block_rate:.3f}

The baseline approves without a browser check because no applicable memory rule is
available. The memory-guided strategy resolves an active behavioral rule and runs the
same real Chromium interaction against every scenario.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmark-results/playwright"),
    )
    args = parser.parse_args()
    report = run_benchmark()
    json_path, markdown_path = write_report(report, args.output)
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")
    print(
        "prevention_rate="
        f"{report.repeated_defect_prevention_rate:.3f}, "
        f"false_block_rate={report.memory_false_block_rate:.3f}"
    )
    return int(
        report.repeated_defect_prevention_rate < 1.0
        or report.memory_false_block_rate > 0.0
    )


if __name__ == "__main__":
    raise SystemExit(main())
