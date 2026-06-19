"""Measure repeated-defect prevention from remembered QA rules in Chromium."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import mean

from osoznanie.memory import MemoryObject, MemoryType, resolve_active_memory
from osoznanie.playwright_runner import PlaywrightBrowserCheckRunner, PlaywrightCheckInput

from .playwright_pages import benchmark_checkout_server

CASES = (True, False, True, False, True, False)


def remembered_rule(at: datetime) -> MemoryObject:
    return MemoryObject(
        id="mem_benchmark_checkout_rule",
        memory_key="qa.rules.checkout.browser",
        memory_type=MemoryType.BEHAVIORAL_RULE,
        content={
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


def run_benchmark() -> dict[str, object]:
    now = datetime.now(UTC)
    defects = sum(CASES)
    healthy = len(CASES) - defects
    rule = remembered_rule(now)
    governing = resolve_active_memory([rule], memory_key=rule.memory_key, at=now)
    if governing is None:
        raise RuntimeError("active benchmark rule was not resolved")

    runner = PlaywrightBrowserCheckRunner()
    missed = 0
    false_blocks = 0
    durations: list[int] = []
    with benchmark_checkout_server() as base_url:
        for index, buggy in enumerate(CASES):
            page_kind = "buggy" if buggy else "healthy"
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
            missed += int(buggy and not blocked)
            false_blocks += int(not buggy and blocked)

    prevented = defects - missed
    return {
        "generated_at": now.isoformat(),
        "scenarios": len(CASES),
        "repeated_defects": defects,
        "healthy_scenarios": healthy,
        "baseline": {
            "strategy": "no_memory_no_browser_check",
            "checks_executed": 0,
            "missed_defects": defects,
            "false_blocks": 0,
            "mean_browser_latency_ms": 0.0,
        },
        "memory_guided": {
            "strategy": "active_memory_rule_plus_playwright",
            "checks_executed": len(CASES),
            "missed_defects": missed,
            "false_blocks": false_blocks,
            "mean_browser_latency_ms": round(mean(durations), 2),
        },
        "prevented_repeat_defects": prevented,
        "repeated_defect_prevention_rate": prevented / defects,
        "memory_false_block_rate": false_blocks / healthy,
    }


def write_report(report: dict[str, object], output: Path) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "playwright-memory-report.json"
    markdown_path = output / "playwright-memory-report.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    baseline = report["baseline"]
    memory = report["memory_guided"]
    lines = [
        "# Playwright memory-effect benchmark",
        "",
        f"- Baseline missed defects: {baseline['missed_defects']}",
        f"- Memory-guided missed defects: {memory['missed_defects']}",
        f"- Prevented repeat defects: {report['prevented_repeat_defects']}",
        f"- Prevention rate: {report['repeated_defect_prevention_rate']:.3f}",
        f"- False-block rate: {report['memory_false_block_rate']:.3f}",
        f"- Browser checks: {memory['checks_executed']}",
        f"- Mean browser latency, ms: {memory['mean_browser_latency_ms']:.2f}",
        "",
    ]
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, markdown_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("benchmark-results/playwright"))
    args = parser.parse_args()
    report = run_benchmark()
    json_path, markdown_path = write_report(report, args.output)
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")
    return int(
        report["repeated_defect_prevention_rate"] < 1.0
        or report["memory_false_block_rate"] > 0.0
    )


if __name__ == "__main__":
    raise SystemExit(main())
