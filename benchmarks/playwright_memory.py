"""Real-browser benchmark for repeated-defect prevention through memory."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from statistics import mean
from threading import Thread

from osoznanie.memory import MemoryObject, MemoryType, resolve_active_memory
from osoznanie.playwright_runner import (
    BrowserCheckEvidence,
    PlaywrightBrowserCheckRunner,
    PlaywrightCheckInput,
)


class BenchmarkStrategy(StrEnum):
    BASELINE = "baseline"
    MEMORY_GUIDED = "memory_guided"


@dataclass(frozen=True)
class BrowserScenario:
    scenario_id: str
    defect_present: bool


@dataclass(frozen=True)
class BrowserScenarioResult:
    scenario_id: str
    strategy: str
    defect_present: bool
    blocked: bool
    check_code: str
    duration_ms: int
    memory_rule_applied: bool


@dataclass(frozen=True)
class BrowserAggregate:
    strategy: str
    repeated_defects: int
    prevented_defects: int
    missed_defects: int
    false_blocks: int
    checks_executed: int
    prevention_rate: float
    false_block_rate: float
    mean_latency_ms: float


@dataclass(frozen=True)
class PlaywrightMemoryReport:
    generated_at: str
    scenarios: int
    prevention_lift: float
    results: list[BrowserScenarioResult]
    aggregates: list[BrowserAggregate]


class CheckoutBenchmarkHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        broken = self.path.startswith("/broken")
        checkout_onclick = (
            "" if broken else "document.querySelector('#confirmation').style.display='block'"
        )
        body = f"""
        <!doctype html>
        <html>
          <body>
            <button id='smoke' onclick="document.querySelector('#smoke-ok').style.display='block'">
              Smoke
            </button>
            <div id='smoke-ok' style='display:none'>Loaded</div>
            <button id='checkout' onclick=\"{checkout_onclick}\">Checkout</button>
            <div id='confirmation' style='display:none'>Order confirmed</div>
          </body>
        </html>
        """
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        del format, args


@contextmanager
def checkout_benchmark_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), CheckoutBenchmarkHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def build_scenarios() -> list[BrowserScenario]:
    return [
        BrowserScenario("clean-1", False),
        BrowserScenario("broken-1", True),
        BrowserScenario("clean-2", False),
        BrowserScenario("broken-2", True),
        BrowserScenario("broken-3", True),
        BrowserScenario("clean-3", False),
        BrowserScenario("broken-4", True),
        BrowserScenario("clean-4", False),
    ]


def build_checkout_memory(now: datetime) -> MemoryObject:
    return MemoryObject(
        id="mem_checkout_click_regression",
        memory_key="qa.rules.checkout.click-path",
        memory_type=MemoryType.BEHAVIORAL_RULE,
        content={
            "trigger_components": ["checkout-button"],
            "action_selector": "#checkout",
            "success_selector": "#confirmation",
        },
        source_event_ids=["evt_prior_checkout_escape"],
        confidence=0.99,
        importance=1.0,
        valid_from=now,
        created_at=now,
        updated_at=now,
    )


def run_playwright_memory_benchmark() -> PlaywrightMemoryReport:
    runner = PlaywrightBrowserCheckRunner()
    now = datetime.now(UTC)
    rule = build_checkout_memory(now)
    results: list[BrowserScenarioResult] = []

    with checkout_benchmark_server() as base_url:
        for scenario in build_scenarios():
            path = "broken" if scenario.defect_present else "clean"
            target_url = f"{base_url}/{path}/{scenario.scenario_id}"
            results.append(
                _run_strategy(
                    runner,
                    scenario,
                    target_url,
                    BenchmarkStrategy.BASELINE,
                    memories=[],
                    now=now,
                )
            )
            results.append(
                _run_strategy(
                    runner,
                    scenario,
                    target_url,
                    BenchmarkStrategy.MEMORY_GUIDED,
                    memories=[rule],
                    now=now,
                )
            )

    aggregates = [
        _aggregate(results, BenchmarkStrategy.BASELINE),
        _aggregate(results, BenchmarkStrategy.MEMORY_GUIDED),
    ]
    by_strategy = {item.strategy: item for item in aggregates}
    prevention_lift = (
        by_strategy[BenchmarkStrategy.MEMORY_GUIDED.value].prevention_rate
        - by_strategy[BenchmarkStrategy.BASELINE.value].prevention_rate
    )
    return PlaywrightMemoryReport(
        generated_at=datetime.now(UTC).isoformat(),
        scenarios=len(build_scenarios()),
        prevention_lift=prevention_lift,
        results=results,
        aggregates=aggregates,
    )


def _run_strategy(
    runner: PlaywrightBrowserCheckRunner,
    scenario: BrowserScenario,
    target_url: str,
    strategy: BenchmarkStrategy,
    *,
    memories: list[MemoryObject],
    now: datetime,
) -> BrowserScenarioResult:
    rule = resolve_active_memory(
        memories,
        memory_key="qa.rules.checkout.click-path",
        at=now,
    )
    memory_rule_applied = strategy is BenchmarkStrategy.MEMORY_GUIDED and rule is not None
    if memory_rule_applied:
        action_selector = str(rule.content["action_selector"])
        success_selector = str(rule.content["success_selector"])
    else:
        action_selector = "#smoke"
        success_selector = "#smoke-ok"

    evidence = runner.run(
        PlaywrightCheckInput(
            release_id=scenario.scenario_id,
            target_url=target_url,
            action_selector=action_selector,
            success_selector=success_selector,
            changed_components=["checkout-button"],
            timeout_ms=2_000,
        )
    )
    return _result(scenario, strategy, evidence, memory_rule_applied)


def _result(
    scenario: BrowserScenario,
    strategy: BenchmarkStrategy,
    evidence: BrowserCheckEvidence,
    memory_rule_applied: bool,
) -> BrowserScenarioResult:
    return BrowserScenarioResult(
        scenario_id=scenario.scenario_id,
        strategy=strategy.value,
        defect_present=scenario.defect_present,
        blocked=not evidence.passed,
        check_code=evidence.code.value,
        duration_ms=evidence.duration_ms,
        memory_rule_applied=memory_rule_applied,
    )


def _aggregate(
    results: list[BrowserScenarioResult],
    strategy: BenchmarkStrategy,
) -> BrowserAggregate:
    selected = [item for item in results if item.strategy == strategy.value]
    defects = [item for item in selected if item.defect_present]
    clean = [item for item in selected if not item.defect_present]
    prevented = sum(item.blocked for item in defects)
    false_blocks = sum(item.blocked for item in clean)
    return BrowserAggregate(
        strategy=strategy.value,
        repeated_defects=len(defects),
        prevented_defects=prevented,
        missed_defects=len(defects) - prevented,
        false_blocks=false_blocks,
        checks_executed=len(selected),
        prevention_rate=prevented / len(defects) if defects else 0.0,
        false_block_rate=false_blocks / len(clean) if clean else 0.0,
        mean_latency_ms=mean(item.duration_ms for item in selected),
    )


def write_playwright_memory_report(
    report: PlaywrightMemoryReport,
    output_dir: Path,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "playwright-memory-report.json"
    markdown_path = output_dir / "playwright-memory-report.md"
    json_path.write_text(
        json.dumps(asdict(report), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    rows = [
        "# Playwright memory-effect benchmark",
        "",
        f"Scenarios: {report.scenarios}",
        f"Prevention lift: {report.prevention_lift:.3f}",
        "",
        "| Strategy | Prevention | Missed | False blocks | Mean latency ms |",
        "|---|---:|---:|---:|---:|",
    ]
    for item in report.aggregates:
        rows.append(
            f"| {item.strategy} | {item.prevention_rate:.3f} | "
            f"{item.missed_defects} | {item.false_blocks} | "
            f"{item.mean_latency_ms:.1f} |"
        )
    markdown_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return json_path, markdown_path
