# Playwright QA adapter and memory-effect benchmark

Install the optional browser dependency and Chromium:

```bash
python -m pip install -e ".[dev,browser]"
python -m playwright install --with-deps chromium
```

Run the real browser integration test:

```bash
pytest -m playwright
```

Generate the controlled memory-effect report:

```bash
python -m benchmarks.run_playwright_memory \
  --output benchmark-results/playwright
```

The benchmark serves six deterministic local checkout pages: three contain the
previously observed click-path regression and three are healthy.

Two strategies receive the same scenarios:

1. `no_memory_no_browser_check` has no applicable remembered rule and approves
   without executing a browser check.
2. `active_memory_rule_plus_playwright` resolves an active behavioral-rule memory
   and executes the same real Chromium interaction for every scenario.

The report records:

- missed repeated defects;
- prevented repeat defects;
- repeated-defect prevention rate;
- false release blocks;
- browser checks executed;
- mean browser latency.

The CI job fails unless the controlled fixture reaches a prevention rate of 1.0
and a false-block rate of 0.0. These values are acceptance thresholds for the
fixture, not claims about performance on arbitrary production websites.

The adapter persists only a sanitized target without query parameters, the check
code, duration, browser, release identifier, and changed components. The full
protected input remains outside the durable action intent and protocol records.
